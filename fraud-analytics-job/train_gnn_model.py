"""
train_gnn_model.py — GNN training stage (ADR-0002)
===================================================
Stage 3 of the fraud-analytics-job pipeline, registered in run_job.py STAGES
after train_fraud_model.

Per tenant:
    1. Load labels via labels.py (shared with the Random Forest).
    2. Build the per-tenant heterogeneous graph from dbo.Nominations / dbo.Users.
    3. Train the encoder + decoder end to end with a three-window temporal split.
    4. Publish per-user node embeddings to dbo.GNN_UserEmbeddings.
    5. Score every in-window nomination into dbo.GNN_FraudScores.
    6. Upload gnn_encoder_tenant_<N>.pt (audit) and gnn_head_tenant_<N>.pt (inference).
    7. Evict node embeddings older than the retention window.

Ordering rationale
------------------
Runs after train_fraud_model so it sees the label view the Random Forest just
refreshed, and so a GNN failure can never block the Random Forest retrain — the
per-stage try/except in run_job.run_stage() provides that isolation. The cost is
that sync_holidays and forecast_models run later in the weekly window.

No post-hook. The backend does not consume the GNN, so
/api/internal/refresh-fraud-model is not called; integrity-check streams the
decoder itself on first use per tenant.

Shadow mode
-----------
Rows are written with ScoringMode from the tenant's integrity_config
(default 'shadow'). Nothing here influences routing.
"""

from __future__ import annotations

import io
import logging
import os
import resource
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import torch
from dotenv import load_dotenv

# Same .env loading as the other stages so this can be run standalone locally.
# No-op in Container Apps, where env is injected by the platform.
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

import gnn_graph as G
import labels as labels_mod
from db_conn import connect
from gnn_model import pr_auc, roc_auc, train_gnn

# Reuse the Random Forest's blob upload helper rather than duplicating the auth
# and error handling. Both stages run in the same process under run_job.py.
from train_fraud_model import _upload_artefact

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent / "Output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Tunables (Terraform-injected) ─────────────────────────────────────────────
GNN_ENABLED                  = os.getenv("GNN_ENABLED", "true").lower() != "false"
GNN_HIDDEN_DIM               = int(os.getenv("GNN_HIDDEN_DIM", "64"))
GNN_EMBED_DIM                = int(os.getenv("GNN_EMBED_DIM", "64"))
GNN_EPOCHS                   = int(os.getenv("GNN_EPOCHS", "300"))
GNN_WINDOW_DAYS              = int(os.getenv("GNN_WINDOW_DAYS", os.getenv("DETECTION_WINDOW_DAYS", "180")))
GNN_EMBEDDING_RETENTION_DAYS = int(os.getenv("GNN_EMBEDDING_RETENTION_DAYS", "90"))

# Below these a per-tenant graph carries too little structure to learn from.
# Synthetic validation showed message passing losing to a flat-feature baseline
# on the smaller of two tenant sizes, so this gate is empirical, not decorative.
MIN_NOMINATIONS = int(os.getenv("GNN_MIN_TRAINING_SAMPLES", "300"))
MIN_USERS       = int(os.getenv("GNN_MIN_USERS", "50"))
# A model trained on a handful of positives is noise with a confidence interval.
MIN_POSITIVES   = int(os.getenv("GNN_MIN_POSITIVES", "10"))


# ── Tenant discovery ──────────────────────────────────────────────────────────

def _container_memory_limit_bytes() -> int | None:
    """
    The cgroup memory ceiling this process is actually running under.

    Read from cgroup rather than inferred from Terraform, because those two are
    exactly the pair that drifts. /proc/meminfo reports the HOST's memory in a
    container, so it cannot be used here.
    """
    for path, parse in (
        ("/sys/fs/cgroup/memory.max", lambda v: None if v.strip() == "max" else int(v)),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", int),
    ):
        try:
            with open(path) as fh:
                limit = parse(fh.read())
            # cgroup v1 reports a sentinel near 2^63 when unlimited.
            if limit and limit < (1 << 62):
                return limit
        except (OSError, ValueError):
            continue
    return None


def _log_peak_rss(label: str) -> float:
    """
    Log peak RSS against the container limit and return peak GiB.

    ADR-0002 sized this job at 4 vCPU / 8 GiB. That number was a precaution, not
    a measurement — nobody had observed what the stage actually uses. Azure bills
    allocated resources, not utilisation, and Consumption locks memory at 2 GiB
    per vCPU, so the memory figure drags the vCPU count along with it. This line
    is what makes the next sizing decision evidence rather than another guess.

    ru_maxrss is high-water for the whole process, so it includes the RF stage
    and the sentence-transformer that ran before this one. That is the right
    number for sizing a container, which is billed on the peak, not on the GNN's
    marginal share.
    """
    peak_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2)  # ru_maxrss is KiB on Linux
    limit = _container_memory_limit_bytes()
    if limit:
        limit_gib = limit / (1024 ** 3)
        pct = peak_gib / limit_gib * 100
        logger.info("MEMORY %s — peak RSS %.2f GiB of %.2f GiB limit (%.0f%%)",
                    label, peak_gib, limit_gib, pct)
        if pct >= 85:
            logger.warning(
                "MEMORY %s — peak RSS is %.0f%% of the container limit. The next "
                "tenant or a larger graph may OOM. Raise cpu/memory in the "
                "fraud-analytics-job Terraform module before that happens.",
                label, pct,
            )
    else:
        logger.info("MEMORY %s — peak RSS %.2f GiB (no cgroup limit visible)", label, peak_gib)
    return peak_gib


def _get_tenants(conn) -> list[int]:
    cur = conn.cursor()
    cur.execute("SELECT TenantId FROM dbo.Tenants ORDER BY TenantId")
    return [r[0] for r in cur.fetchall()]


def _scoring_mode(conn, tenant_id: int) -> str:
    """Read integrity_config.gnn.mode. Anything unrecognised means shadow."""
    import json
    cur = conn.cursor()
    cur.execute("SELECT integrity_config FROM dbo.Tenants WHERE TenantId = ?", tenant_id)
    row = cur.fetchone()
    if not row or not row[0]:
        return "shadow"
    try:
        mode = str((json.loads(row[0]).get("gnn", {}) or {}).get("mode", "shadow")).lower()
    except (json.JSONDecodeError, TypeError, AttributeError):
        return "shadow"
    return mode if mode in ("shadow", "active") else "shadow"


# ── Persistence ───────────────────────────────────────────────────────────────
# Temp table + single MERGE per table, mirroring score_and_save_historical():
# three SQL round-trips regardless of row count, rather than N.

def _publish_embeddings(
    conn, tenant_id: int, user_ids: list[int], z: np.ndarray,
    as_of: date, model_version: str,
) -> int:
    cur = conn.cursor()
    cur.fast_executemany = True

    cur.execute("""
        CREATE TABLE #gnn_emb (
            UserId INT, AsOfDate DATE, Embedding VARBINARY(MAX),
            EmbeddingDim SMALLINT, ModelVersion VARCHAR(64)
        )
    """)
    rows = [
        (int(uid), as_of, z[i].astype(np.float32).tobytes(), int(z.shape[1]), model_version)
        for i, uid in enumerate(user_ids)
    ]
    cur.executemany(
        "INSERT INTO #gnn_emb (UserId, AsOfDate, Embedding, EmbeddingDim, ModelVersion) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    cur.execute("""
        MERGE dbo.GNN_UserEmbeddings AS target
        USING (SELECT ? AS TenantId, UserId, AsOfDate, Embedding, EmbeddingDim, ModelVersion
               FROM #gnn_emb) AS src
            ON  target.TenantId = src.TenantId
            AND target.UserId   = src.UserId
            AND target.AsOfDate = src.AsOfDate
        WHEN MATCHED THEN
            UPDATE SET Embedding = src.Embedding, EmbeddingDim = src.EmbeddingDim,
                       ModelVersion = src.ModelVersion, LastUpdatedUtc = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN
            INSERT (TenantId, UserId, AsOfDate, Embedding, EmbeddingDim, ModelVersion)
            VALUES (src.TenantId, src.UserId, src.AsOfDate, src.Embedding,
                    src.EmbeddingDim, src.ModelVersion);
    """, tenant_id)
    cur.execute("DROP TABLE #gnn_emb")
    conn.commit()
    return len(rows)


def _save_scores(
    conn, nomination_ids: list[int], probs: np.ndarray, thresholds: dict,
    model_version: str, embedding_as_of: date, scoring_mode: str,
) -> int:
    cur = conn.cursor()
    cur.fast_executemany = True

    scores = np.clip((probs * 100).round().astype(int), 0, 100)
    levels = np.full(len(scores), "NONE", dtype=object)
    levels[scores >= thresholds["low"]]      = "LOW"
    levels[scores >= thresholds["medium"]]   = "MEDIUM"
    levels[scores >= thresholds["high"]]     = "HIGH"
    levels[scores >= thresholds["critical"]] = "CRITICAL"

    cur.execute("""
        CREATE TABLE #gnn_scores (
            NominationId INT, FraudScore INT, FraudProbability FLOAT,
            RiskLevel VARCHAR(20), ModelVersion VARCHAR(64),
            EmbeddingAsOfDate DATE, ScoringMode VARCHAR(10), ScoredBy NVARCHAR(256)
        )
    """)
    rows = [
        (int(nid), int(scores[i]), float(probs[i]), str(levels[i]),
         model_version, embedding_as_of, scoring_mode, "svc:fraud-analytics-job")
        for i, nid in enumerate(nomination_ids)
    ]
    cur.executemany(
        "INSERT INTO #gnn_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    cur.execute("""
        MERGE dbo.GNN_FraudScores AS target
        USING #gnn_scores AS src
            ON  target.NominationId = src.NominationId
            AND target.ModelVersion = src.ModelVersion
        WHEN MATCHED THEN
            UPDATE SET FraudScore = src.FraudScore, FraudProbability = src.FraudProbability,
                       RiskLevel = src.RiskLevel, EmbeddingAsOfDate = src.EmbeddingAsOfDate,
                       ScoringMode = src.ScoringMode, ScoredBy = src.ScoredBy
        WHEN NOT MATCHED THEN
            INSERT (NominationId, FraudScore, FraudProbability, RiskLevel,
                    ModelVersion, EmbeddingAsOfDate, ScoringMode, ScoredBy)
            VALUES (src.NominationId, src.FraudScore, src.FraudProbability, src.RiskLevel,
                    src.ModelVersion, src.EmbeddingAsOfDate, src.ScoringMode, src.ScoredBy);
    """)
    cur.execute("DROP TABLE #gnn_scores")
    conn.commit()
    return len(rows)


def _evict_stale_embeddings(conn, tenant_id: int, retention_days: int) -> int:
    """Bound table growth. Retention must outlive the rollback window it protects."""
    cutoff = date.today() - timedelta(days=retention_days)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM dbo.GNN_UserEmbeddings WHERE TenantId = ? AND AsOfDate < ?",
        tenant_id, cutoff,
    )
    n = cur.rowcount
    conn.commit()
    return max(n, 0)


# ── Artifacts ─────────────────────────────────────────────────────────────────

def _write_head(model, graph: dict, model_version: str, metrics: dict, path: Path) -> None:
    """
    Serialise the decoder — the only artifact integrity-check downloads.

    Every value here must be a torch tensor or a Python primitive. gnn_check.py
    loads with torch.load(weights_only=True), which rejects numpy's array
    reconstructor, so scalers are lists of floats rather than ndarrays. That
    restriction is what stops a .pt file being as executable as a .pkl — do not
    add a richer object here to save a conversion.
    """
    head = {
        "decoder_state_dict":         model.decoder.net.state_dict(),
        "decoder_hidden":             [64, 32],
        "emb_dim":                    int(model.emb_dim),
        "model_version":              model_version,
        "nomination_feature_columns": list(G.NOMINATION_FEATURE_COLUMNS),
        "nomination_scaler_mean":     [float(v) for v in graph["nomination_scaler"]["mean"]],
        "nomination_scaler_std":      [float(v) for v in graph["nomination_scaler"]["std"]],
        # Persisted for reproducibility only. gnn_check.py must NOT apply these:
        # the embeddings it reads are encoder OUTPUT, already past this transform.
        "user_scaler_mean":           [float(v) for v in graph["user_scaler"]["mean"]],
        "user_scaler_std":            [float(v) for v in graph["user_scaler"]["std"]],
        "amount_mean":                float(graph["amount_mean"]),
        "amount_std":                 float(graph["amount_std"]),
        "metrics":                    {k: (float(v) if isinstance(v, (int, float)) else str(v))
                                       for k, v in metrics.items() if k != "history"},
    }
    torch.save(head, path)

    # Fail here rather than in production: prove the artifact we just wrote can
    # be read back under the same restriction inference will use.
    with open(path, "rb") as f:
        torch.load(io.BytesIO(f.read()), map_location="cpu", weights_only=True)


# ── Per-tenant run ────────────────────────────────────────────────────────────

def _process_tenant(conn, tenant_id: int) -> str:
    t0 = time.monotonic()

    users, nominations = G.fetch_tenant_rows(conn, tenant_id, GNN_WINDOW_DAYS)
    if len(nominations) < MIN_NOMINATIONS or len(users) < MIN_USERS:
        return (f"SKIPPED (below gate: {len(nominations)} nominations / {len(users)} users, "
                f"need {MIN_NOMINATIONS}/{MIN_USERS})")

    label_df = labels_mod.load_labels(conn, tenant_id, window_days=GNN_WINDOW_DAYS)
    stats = labels_mod.summarise(label_df, tenant_id)

    # The GNN excludes unlabelled rows from its TARGETS — deliberately diverging
    # from the Random Forest, which treats them as legitimate. They remain in the
    # graph as edges; they simply are not trained against. See labels.py: a row
    # with no P2P score is unlabelled, not clean, and this model has no legacy
    # behaviour to preserve.
    labelled = label_df[label_df["LabelSource"] != labels_mod.SOURCE_UNLABELLED]
    label_map = dict(zip(labelled["NominationId"], labelled["IsFraud"]))
    if not label_map:
        return "SKIPPED (no labelled nominations)"

    graph = G.build_hetero_data(users, nominations)

    def _y(split: str):
        ids = graph[split]["nom_ids"]
        keep = [i for i, nid in enumerate(ids) if nid in label_map]
        return keep, np.array([label_map[ids[i]] for i in keep])

    keep_tr, y_tr = _y("train")
    keep_ev, y_ev = _y("eval")
    for split, keep in (("train", keep_tr), ("eval", keep_ev)):
        graph[split]["nom_ids"] = [graph[split]["nom_ids"][i] for i in keep]
        graph[split]["x"]       = graph[split]["x"][keep]
        graph[split]["triples"] = graph[split]["triples"][keep]

    if int(y_tr.sum()) < MIN_POSITIVES or int(y_ev.sum()) < MIN_POSITIVES:
        return (f"SKIPPED (too few positives: train {int(y_tr.sum())}, "
                f"eval {int(y_ev.sum())}, need {MIN_POSITIVES} each)")

    model, metrics = train_gnn(
        graph, y_tr, y_ev,
        hidden_dim=GNN_HIDDEN_DIM, emb_dim=GNN_EMBED_DIM, epochs=GNN_EPOCHS,
    )

    # Stratified metrics — ADR-0002 criterion 2. PR-AUC on the human-confirmed
    # subset is the only number that establishes the GNN adds anything; PR-AUC on
    # model-derived labels measures agreement with the Random Forest and nothing
    # more. They are reported separately so they can never be conflated.
    hrbp_ids = set(labelled.loc[labelled["LabelSource"] == labels_mod.SOURCE_HRBP, "NominationId"])
    with torch.no_grad():
        z_all     = model.embed_users(graph["data"])
        ev_logits = model.score(z_all, graph["eval"]["triples"], graph["eval"]["x"]).numpy()
    mask = np.array([nid in hrbp_ids for nid in graph["eval"]["nom_ids"]])

    # Gate on POSITIVES, not on rows. A hundred human-confirmed rows that are all
    # legitimate cannot produce a PR-AUC — the metric is undefined with one class —
    # and gating on row count lets that through as a silent nan in the summary
    # line. This is ADR-0002 criterion 2, the only metric that establishes the GNN
    # adds anything, so it fails out loud or not at all.
    n_hrbp_rows = int(mask.sum())
    n_hrbp_pos  = int(y_ev[mask].sum()) if n_hrbp_rows else 0
    if n_hrbp_pos >= MIN_POSITIVES and n_hrbp_pos < n_hrbp_rows:
        metrics["eval_pr_auc_hrbp"] = pr_auc(y_ev[mask], ev_logits[mask])
    else:
        metrics["eval_pr_auc_hrbp"] = float("nan")
        logger.warning(
            "[Tenant %d] human-confirmed PR-AUC NOT COMPUTABLE — %d confirmed rows in the "
            "evaluation window carrying %d fraud labels (need >= %d, and at least one of "
            "each class). Until this is satisfiable the ADR-0002 evaluation gate cannot be "
            "assessed for this tenant, whatever the all-label metric says.",
            tenant_id, n_hrbp_rows, n_hrbp_pos, MIN_POSITIVES,
        )
    metrics["n_eval_hrbp"]     = n_hrbp_rows
    metrics["n_eval_hrbp_pos"] = n_hrbp_pos

    _hrbp_str = (f"{metrics['eval_pr_auc_hrbp']:.4f}"
                 if not np.isnan(metrics["eval_pr_auc_hrbp"]) else "n/a")
    logger.info(
        "[Tenant %d] PR-AUC eval %.4f (all labels) | %s (human-confirmed, n=%d, pos=%d) | ROC %.4f",
        tenant_id, metrics["eval_pr_auc"], _hrbp_str,
        n_hrbp_rows, n_hrbp_pos, metrics["eval_roc_auc"],
    )

    as_of = date.today()
    model_version = f"gnn-{as_of:%Y%m%d}-t{tenant_id}"

    with torch.no_grad():
        z = model.embed_users(graph["data"]).numpy().astype(np.float32)
    user_ids = sorted(graph["user_index"], key=lambda u: graph["user_index"][u])
    n_emb = _publish_embeddings(conn, tenant_id, user_ids, z, as_of, model_version)

    # Score every in-window nomination, not just the eval split — historical
    # coverage is what the shadow-mode comparison is run against.
    # Collect ids, rows and triples in one pass so the three stay index-aligned
    # by construction. Rebuilding the row list from a separate filter would work
    # today only because both preserve `nominations` order — a silent coupling
    # that a later reorder would break without any error.
    all_ids, all_rows, all_triples = [], [], []
    ui = graph["user_index"]
    for n in nominations:
        if n["NominatorId"] not in ui or n["BeneficiaryId"] not in ui:
            continue
        all_ids.append(n["NominationId"])
        all_rows.append(n)
        all_triples.append((ui[n["NominatorId"]], ui[n["BeneficiaryId"]],
                            ui.get(n["ApproverId"], -1) if n.get("ApproverId") is not None else -1))

    all_x = torch.from_numpy(G._apply(
        G.build_nomination_features(all_rows, graph["amount_mean"], graph["amount_std"]),
        graph["nomination_scaler"]["mean"], graph["nomination_scaler"]["std"],
    ))
    with torch.no_grad():
        probs = torch.sigmoid(
            model.score(z_all, torch.tensor(all_triples, dtype=torch.long), all_x)
        ).numpy()

    mode = _scoring_mode(conn, tenant_id)
    thresholds = {"critical": 85, "high": 65, "medium": 45, "low": 25}
    n_scores = _save_scores(conn, all_ids, probs, thresholds, model_version, as_of, mode)

    enc_path  = OUTPUT_DIR / f"gnn_encoder_tenant_{tenant_id}.pt"
    head_path = OUTPUT_DIR / f"gnn_head_tenant_{tenant_id}.pt"
    torch.save({"encoder_state_dict": model.encoder.state_dict(),
                "model_version": model_version,
                "emb_dim": int(model.emb_dim)}, enc_path)
    _write_head(model, graph, model_version, metrics, head_path)

    # Head last: until it lands, inference finds no decoder for this version and
    # scores nothing, which is the safe state. Uploading it first would briefly
    # expose a decoder whose embeddings are not yet published.
    _upload_artefact(enc_path)
    _upload_artefact(head_path)

    n_evicted = _evict_stale_embeddings(conn, tenant_id, GNN_EMBEDDING_RETENTION_DAYS)

    return (f"OK ({model_version}, {n_emb} embeddings, {n_scores} scores, "
            f"{n_evicted} evicted, PR-AUC {metrics['eval_pr_auc']:.4f}, "
            f"human-label PR-AUC {_hrbp_str}, "
            f"mode={mode}, {time.monotonic() - t0:.1f}s)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main(tenants_to_process: list | None = None) -> None:
    """Called by run_job.py. Signature matches every other stage."""
    if not GNN_ENABLED:
        logger.info("GNN_ENABLED=false — skipping GNN training stage.")
        return

    logger.info("=" * 60)
    logger.info("GNN MODEL TRAINING — Multi-Tenant (ADR-0002)")
    logger.info("=" * 60)
    logger.info("Window: %d days | hidden %d | emb %d | epochs %d | retention %d days",
                GNN_WINDOW_DAYS, GNN_HIDDEN_DIM, GNN_EMBED_DIM,
                GNN_EPOCHS, GNN_EMBEDDING_RETENTION_DAYS)

    conn = connect()
    try:
        tenants = _get_tenants(conn)
        if tenants_to_process is not None:
            tenants = [t for t in tenants if t in tenants_to_process]
            if not tenants:
                logger.warning("Tenant(s) %s not found. Exiting.", tenants_to_process)
                return
        logger.info("Tenants: %s", tenants)

        results, failed = {}, []
        for tenant_id in tenants:
            logger.info("── Tenant %d ─────────────────────────────────", tenant_id)
            try:
                results[tenant_id] = _process_tenant(conn, tenant_id)
            except Exception as exc:
                logger.error("Tenant %d failed: %s", tenant_id, exc, exc_info=True)
                results[tenant_id] = f"FAILED — {exc}"
                failed.append(tenant_id)
            finally:
                _log_peak_rss(f"after tenant {tenant_id}")
    finally:
        conn.close()

    logger.info("")
    _log_peak_rss("stage total")
    logger.info("GNN TRAINING SUMMARY")
    for tenant_id, status in results.items():
        logger.info("  Tenant %s: %s", tenant_id, status)

    # Raise so run_job marks the stage failed and the Azure Monitor alert fires.
    # A tenant skipped by the sample gate is NOT a failure — that is the designed
    # behaviour for a tenant too small to support a graph model.
    if failed:
        raise RuntimeError(f"GNN training failed for tenant(s): {failed}")


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOGGING_LEVEL", "INFO").upper(),
                        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s")
    main()
