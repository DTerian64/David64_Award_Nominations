"""
train_gnn_model.py — GNN training stage
=========================================
Stage 3 of the fraud-analytics-job pipeline, registered in run_job.py STAGES
after train_rf_model.

Per tenant:
    1. Load labels via labels.py (shared with the Random Forest).
    2. Build the per-tenant heterogeneous graph from dbo.Nominations / dbo.Users.
    3. Train the encoder + decoder end to end with a three-window temporal split.
    4. Publish per-user node embeddings to dbo.GNN_UserEmbeddings.
    5. Upload gnn_encoder_tenant_<N>.pt (audit) and gnn_head_tenant_<N>.pt (inference).
    6. Evict node embeddings older than the retention window.

Ordering rationale
------------------
Runs after train_rf_model for stable operations. Both models independently read
the same human label contract, and a GNN failure cannot block the RF retrain — the
per-stage try/except in run_job.run_stage() provides that isolation. The cost is
that sync_holidays and forecast_models run later in the weekly window.

No post-hook. The backend does not load model artifacts, so
/api/internal/refresh-fraud-model is not called; integrity-check streams the
decoder itself on first use per tenant.
"""

from __future__ import annotations

import io
import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import torch

# Unix-only stdlib. Absent on Windows, where developers run this stage against
# the sandbox database by hand. A hard import here made the whole module
# unimportable on a dev box while working fine in the Linux container — the
# memory reporting below degrades instead.
try:
    import resource
except ImportError:          # Windows
    resource = None
from dotenv import load_dotenv

# Same .env loading as the other stages so this can be run standalone locally.
# No-op in Container Apps, where env is injected by the platform.
JOB_DIR = Path(__file__).resolve().parents[1]
env_path = JOB_DIR.parent / ".env"
load_dotenv(env_path)

from . import gnn_graph as G  # noqa: E402 - .env must load before model imports
from . import labels as labels_mod  # noqa: E402
from .artifact_manifest import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    artifact_descriptor,
    state_dict_summary,
    write_manifest,
)
from utils.component_status import upsert_component_status  # noqa: E402
from utils.db_conn import connect  # noqa: E402
from .gnn_model import _RELATIONS, train_gnn  # noqa: E402

# Reuse the Random Forest's blob upload helper rather than duplicating the auth
# and error handling. Both stages run in the same process under run_job.py.
from .train_rf_model import _upload_artefact  # noqa: E402

logger = logging.getLogger(__name__)

OUTPUT_DIR = JOB_DIR / "Output"
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


def _log_peak_rss(label: str) -> float | None:
    """
    Log peak RSS against the container limit and return peak GiB.

    Returns None where the platform cannot report it (Windows), so callers must
    not assume a float.

    The initial deployment sized this job at 4 vCPU / 8 GiB. That number was a precaution, not
    a measurement — nobody had observed what the stage actually uses. Azure bills
    allocated resources, not utilisation, and Consumption locks memory at 2 GiB
    per vCPU, so the memory figure drags the vCPU count along with it. This line
    is what makes the next sizing decision evidence rather than another guess.

    ru_maxrss is high-water for the whole process, so it includes the RF stage
    and the sentence-transformer that ran before this one. That is the right
    number for sizing a container, which is billed on the peak, not on the GNN's
    marginal share.
    """
    if resource is None:
        logger.info(
            "MEMORY %s — peak RSS unavailable on this platform (the stdlib "
            "'resource' module is Unix-only). Container runs still report it.",
            label,
        )
        return None

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


# ── Persistence ───────────────────────────────────────────────────────────────
# The weekly job publishes only the user embeddings required by live inference.

def _publish_embeddings(
    conn, tenant_id: int, user_ids: list[int], z: np.ndarray,
    as_of: date, model_version: str,
) -> int:
    cur = conn.cursor()

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
    # fast_executemany OFF for this one statement.
    #
    # fast_executemany makes pyodbc pre-bind a single fixed-width buffer per
    # column rather than describing each row, and for a bytes parameter that
    # buffer defaults to 255. A float32 embedding is 4 bytes per dimension, so
    # GNN_EMBED_DIM=64 is exactly 256 bytes and overflows it by one float:
    #     ('String data, right truncation: length 256 buffer 255', 'HY000')
    # The column is VARBINARY(MAX); the limit was entirely client-side. Any
    # embed_dim >= 64 hits it, which is to say the shipped default did.
    #
    # Binding per row costs a round trip per row — a few seconds for a tenant
    # with thousands of users, once a week. If that ever matters, the faster fix
    # is cur.setinputsizes() with an explicit VARBINARY width, but verify the
    # exact call against the pyodbc version in the image first: the placeholder
    # and MAX-size semantics are not documented on the wiki.
    cur.fast_executemany = False   # see note above
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
        "participant_roles":          ["nominator", "beneficiary"],
        "behavior_statuses":          ["Pending", "Approved", "Paid"],
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


def _write_gnn_manifest(
    tenant_id: int,
    model,
    model_version: str,
    metrics: dict,
    encoder_path: Path,
    head_path: Path,
) -> Path:
    """Publish a non-executable representation of the encoder and serving head."""
    manifest_path = OUTPUT_DIR / f"gnn_tenant_{tenant_id}.manifest.json"
    emb_dim = int(model.emb_dim)
    nomination_feature_count = len(G.NOMINATION_FEATURE_COLUMNS)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_type": "graph_neural_network",
        "tenant_id": tenant_id,
        "model_version": model_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "Tenant-scoped heterogeneous GraphSAGE fraud model",
        "architecture": {
            "encoder": {
                "type": "Heterogeneous GraphSAGE",
                "role": "audit_and_retraining",
                "layer_count": len(model.encoder.convs),
                "embedding_dimension": emb_dim,
                "aggregation": "mean",
                "relations": [
                    {"source": source, "relationship": relation, "target": target}
                    for source, relation, target in _RELATIONS
                ],
                **state_dict_summary(model.encoder.state_dict()),
            },
            "decoder": {
                "type": "Multilayer Perceptron",
                "role": "live_inference",
                "input_dimension": 2 * emb_dim + nomination_feature_count,
                "layers": [2 * emb_dim + nomination_feature_count, 64, 32, 1],
                "dropout": 0.2,
                **state_dict_summary(model.decoder.net.state_dict()),
            },
        },
        "features": {
            "user": list(G.USER_FEATURE_COLUMNS),
            "nomination": list(G.NOMINATION_FEATURE_COLUMNS),
            "participant_roles": ["nominator", "beneficiary"],
            "behavior_statuses": ["Pending", "Approved", "Paid"],
        },
        "training": {key: value for key, value in metrics.items() if key != "history"},
        "artifacts": [
            artifact_descriptor(encoder_path, "audit_encoder"),
            artifact_descriptor(head_path, "serving_head"),
        ],
    }
    write_manifest(manifest_path, manifest)
    return manifest_path


# ── Per-tenant run ────────────────────────────────────────────────────────────

def _process_tenant(conn, tenant_id: int, run_id: str | None = None) -> str:
    t0 = time.monotonic()
    run_id = run_id or str(uuid.uuid4())

    users, nominations = G.fetch_tenant_rows(conn, tenant_id, GNN_WINDOW_DAYS)
    behavior_nominations = [
        row for row in nominations
        if bool(row.get("IsBehaviorEligible", True))
    ]
    base_diagnostics = {
        "window_days": GNN_WINDOW_DAYS,
        "nomination_count": len(behavior_nominations),
        "user_count": len(users),
    }
    if len(behavior_nominations) < MIN_NOMINATIONS or len(users) < MIN_USERS:
        detail = (f"{len(behavior_nominations)} nominations / {len(users)} users; "
                  f"requires {MIN_NOMINATIONS} / {MIN_USERS}")
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="BELOW_MINIMUM_VOLUME", reason_detail=detail,
            diagnostics={
                **base_diagnostics,
                "minimum_nominations": MIN_NOMINATIONS, "minimum_users": MIN_USERS,
            },
            run_id=run_id,
        )
        return (f"SKIPPED (below gate: {len(behavior_nominations)} nominations / {len(users)} users, "
                f"need {MIN_NOMINATIONS}/{MIN_USERS})")

    label_df = labels_mod.load_labels(conn, tenant_id, window_days=GNN_WINDOW_DAYS)
    labels_mod.summarise(label_df, tenant_id)

    # True training independence: only human-confirmed HRBP outcomes may enter
    # the GNN loss. Random Forest scores and unexamined rows remain graph edges,
    # but neither is a target. A tenant without enough human outcomes is skipped
    # rather than silently teaching the GNN to reproduce the RF.
    labelled = labels_mod.human_confirmed(label_df)
    label_map = dict(zip(labelled["NominationId"], labelled["IsFraud"]))
    if not label_map:
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="NO_HUMAN_CONFIRMED_LABELS",
            reason_detail="No human-confirmed HRBP outcomes are available for GNN training.",
            diagnostics={**base_diagnostics, "human_confirmed_count": 0},
            run_id=run_id,
        )
        return "SKIPPED (no human-confirmed nominations)"

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
        graph[split]["pairs"] = graph[split]["pairs"][keep]

    train_pos = int(y_tr.sum())
    eval_pos = int(y_ev.sum())
    train_neg = int(len(y_tr) - train_pos)
    eval_neg = int(len(y_ev) - eval_pos)
    if train_pos < MIN_POSITIVES or eval_pos < MIN_POSITIVES:
        detail = (f"train fraud labels {train_pos}, eval fraud labels {eval_pos}; "
                  f"requires {MIN_POSITIVES} in each split")
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="INSUFFICIENT_FRAUD_LABELS", reason_detail=detail,
            diagnostics={
                **base_diagnostics,
                "train_positive_count": train_pos, "eval_positive_count": eval_pos,
                "minimum_positives_per_split": MIN_POSITIVES,
                "train_negative_count": train_neg, "eval_negative_count": eval_neg,
            },
            run_id=run_id,
        )
        return (f"SKIPPED (too few human-confirmed fraud labels: train {train_pos}, "
                f"eval {eval_pos}, need {MIN_POSITIVES} each)")
    if train_neg == 0 or eval_neg == 0:
        detail = (f"train {train_pos} fraud/{train_neg} legitimate; "
                  f"eval {eval_pos} fraud/{eval_neg} legitimate")
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="MISSING_LABEL_CLASS", reason_detail=detail,
            diagnostics={
                **base_diagnostics,
                "train_positive_count": train_pos, "train_negative_count": train_neg,
                "eval_positive_count": eval_pos, "eval_negative_count": eval_neg,
            },
            run_id=run_id,
        )
        return (f"SKIPPED (human-confirmed labels need both classes: "
                f"train {train_pos} fraud/{train_neg} legitimate, "
                f"eval {eval_pos} fraud/{eval_neg} legitimate)")

    model, metrics = train_gnn(
        graph, y_tr, y_ev,
        hidden_dim=GNN_HIDDEN_DIM, emb_dim=GNN_EMBED_DIM, epochs=GNN_EPOCHS,
    )

    # Every target is now human-confirmed, so the ordinary training metrics are
    # the independent metrics. Keep the explicit aliases for existing reports.
    metrics["eval_pr_auc_hrbp"] = metrics["eval_pr_auc"]
    metrics["n_eval_hrbp"] = int(len(y_ev))
    metrics["n_eval_hrbp_pos"] = eval_pos
    logger.info(
        "[Tenant %d] human-confirmed PR-AUC %.4f (n=%d, fraud=%d) | ROC %.4f",
        tenant_id, metrics["eval_pr_auc"], len(y_ev), eval_pos,
        metrics["eval_roc_auc"],
    )

    as_of = date.today()
    model_version = f"gnn-{as_of:%Y%m%d}-t{tenant_id}"

    with torch.no_grad():
        z = model.embed_users(graph["data"]).numpy().astype(np.float32)
    user_ids = sorted(graph["user_index"], key=lambda u: graph["user_index"][u])
    n_emb = _publish_embeddings(conn, tenant_id, user_ids, z, as_of, model_version)

    enc_path  = OUTPUT_DIR / f"gnn_encoder_tenant_{tenant_id}.pt"
    head_path = OUTPUT_DIR / f"gnn_head_tenant_{tenant_id}.pt"
    torch.save({"encoder_state_dict": model.encoder.state_dict(),
                "model_version": model_version,
                "emb_dim": int(model.emb_dim)}, enc_path)
    _write_head(model, graph, model_version, metrics, head_path)
    manifest_path = _write_gnn_manifest(
        tenant_id=tenant_id,
        model=model,
        model_version=model_version,
        metrics=metrics,
        encoder_path=enc_path,
        head_path=head_path,
    )

    # Upload model artifacts encoder first and head second: until the head lands,
    # inference finds no decoder for this version and scores nothing. The JSON
    # representation is presentation metadata and is published only afterward.
    _upload_artefact(enc_path)
    _upload_artefact(head_path)
    _upload_artefact(manifest_path)

    n_evicted = _evict_stale_embeddings(conn, tenant_id, GNN_EMBEDDING_RETENTION_DAYS)

    upsert_component_status(
        conn, tenant_id=tenant_id, component="GNN", attempt_status="SUCCEEDED",
        serving_status="AVAILABLE", serving_version=model_version,
        serving_as_of=as_of, run_id=run_id,
        diagnostics={
            **base_diagnostics,
            "embedding_count": n_emb,
            "evicted_embedding_count": n_evicted,
            "human_label_pr_auc": metrics["eval_pr_auc"],
            "human_confirmed_eval_count": len(y_ev),
        },
    )

    return (f"OK ({model_version}, {n_emb} embeddings, "
            f"{n_evicted} evicted, human-label PR-AUC "
            f"{metrics['eval_pr_auc']:.4f}, {time.monotonic() - t0:.1f}s)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main(tenants_to_process: list | None = None) -> None:
    """Called by run_job.py. Signature matches every other stage."""
    run_id = str(uuid.uuid4())
    if not GNN_ENABLED:
        logger.info("GNN_ENABLED=false — skipping GNN training stage.")
        conn = connect()
        try:
            tenants = _get_tenants(conn)
            if tenants_to_process is not None:
                tenants = [t for t in tenants if t in tenants_to_process]
            for tenant_id in tenants:
                upsert_component_status(
                    conn, tenant_id=tenant_id, component="GNN",
                    attempt_status="DISABLED", reason_code="DISABLED",
                    reason_detail="GNN_ENABLED=false for this analytics job run.",
                    diagnostics={"gnn_enabled": False}, run_id=run_id,
                )
        finally:
            conn.close()
        return

    logger.info("GNN MODEL TRAINING - Multi-Tenant")
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
            logger.info("Tenant %d", tenant_id)
            try:
                results[tenant_id] = _process_tenant(conn, tenant_id, run_id)
            except Exception as exc:
                logger.error("Tenant %d failed: %s", tenant_id, exc, exc_info=True)
                results[tenant_id] = f"FAILED — {exc}"
                failed.append(tenant_id)
                try:
                    conn.rollback()
                    upsert_component_status(
                        conn, tenant_id=tenant_id, component="GNN",
                        attempt_status="FAILED", reason_code="TRAINING_FAILED",
                        reason_detail=str(exc), run_id=run_id,
                    )
                except Exception:
                    logger.error(
                        "Tenant %d GNN failure status could not be persisted",
                        tenant_id, exc_info=True,
                    )
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
