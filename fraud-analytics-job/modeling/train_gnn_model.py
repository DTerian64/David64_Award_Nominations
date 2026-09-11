"""
train_gnn_model.py — GNN training stage
=========================================
Stage 3 of the fraud-analytics-job pipeline, registered in run_job.py STAGES
after train_rf_model.

Per tenant:
    1. Load labels via labels.py (shared with the Random Forest).
    2. Build the per-tenant heterogeneous graph from dbo.Nominations / dbo.Users.
    3. Compare the MLP admission baseline and configured graph architectures.
    4. Select one graph winner by the versioned operational policy.
    5. Refit the winner over all matured labels and publish its embeddings.
    6. Upload the immutable candidate and serving bundle.
    7. Activate it through dbo.IntegrityComponentStatus as the final step.

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

from .gnn import graph as G  # noqa: E402 - .env must load before model imports
from .gnn import artifact_bundle as bundle  # noqa: E402
from . import labels as labels_mod  # noqa: E402
from .artifact_manifest import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    artifact_descriptor,
    write_manifest,
)
from utils.component_status import upsert_component_status  # noqa: E402
from utils.db_conn import connect  # noqa: E402
from .gnn.model import (  # noqa: E402
    _RELATIONS,
    fit_candidate_rolling,
    train_candidate_rolling,
)
from .gnn.selection import (  # noqa: E402
    GRAPH_ARCHITECTURES,
    select_architecture,
)
from .gnn.policy import GNNPolicy, load_active_policy  # noqa: E402

# Reuse the Random Forest's blob upload helper rather than duplicating the auth
# and error handling. Both stages run in the same process under run_job.py.
from .train_rf_model import _upload_artefact  # noqa: E402

logger = logging.getLogger(__name__)

OUTPUT_DIR = JOB_DIR / "Output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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
    # An embedding dimension of 64 is exactly 256 bytes and overflows it by one float:
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
            AND target.ModelVersion = src.ModelVersion
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
    return max(n, 0)


# ── Artifacts ─────────────────────────────────────────────────────────────────

def _write_head(
    model,
    graph: dict,
    model_version: str,
    graph_snapshot_id: str,
    metrics: dict,
    path: Path,
    policy: GNNPolicy,
) -> None:
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
        "architecture":               str(model.architecture),
        "model_version":              model_version,
        "training_policy_id":         policy.policy_id,
        "training_policy_version":    policy.policy_version,
        "feature_schema_version":     graph["feature_schema_version"],
        "graph_snapshot_id":          graph_snapshot_id,
        "graph_snapshot_as_of":       graph.get(
            "graph_snapshot_as_of", graph["t_graph"]
        ).isoformat(),
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
        "category_amount_stats":      graph["category_amount_stats"],
        # Serving needs headline metrics only. Rolling-fold detail and offline
        # candidate comparisons remain in the manifest, avoiding a large and
        # operationally irrelevant decoder artifact.
        "metrics":                    {
            k: (float(v) if isinstance(v, (int, float)) else str(v))
            for k, v in metrics.items()
            if k not in {"history", "folds", "selection", "candidates"}
        },
    }
    torch.save(head, path)

    # Fail here rather than in production: prove the artifact we just wrote can
    # be read back under the same restriction inference will use.
    with open(path, "rb") as f:
        torch.load(io.BytesIO(f.read()), map_location="cpu", weights_only=True)


def _write_encoder(
    model,
    graph: dict,
    model_version: str,
    graph_snapshot_id: str,
    path: Path,
    policy: GNNPolicy,
) -> None:
    """Write one graph encoder as a restricted-deserialization-safe artifact."""
    torch.save({
        "encoder_state_dict": model.encoder.state_dict(),
        "architecture": str(model.architecture),
        "model_version": model_version,
        "training_policy_id": policy.policy_id,
        "training_policy_version": policy.policy_version,
        "emb_dim": int(model.emb_dim),
        "hidden_dim": policy.hidden_dim,
        "num_layers": len(model.encoder.convs),
        "feature_schema_version": graph["feature_schema_version"],
        "graph_snapshot_id": graph_snapshot_id,
        "graph_snapshot_as_of": graph.get(
            "graph_snapshot_as_of", graph["t_graph"]
        ).isoformat(),
        "relations": [list(relation) for relation in _RELATIONS],
    }, path)
    with path.open("rb") as handle:
        torch.load(io.BytesIO(handle.read()), map_location="cpu", weights_only=True)


def _write_operational_manifest(
    *,
    tenant_id: int,
    graph: dict,
    model_version: str,
    graph_snapshot_id: str,
    selection: dict,
    artifact_paths: list[tuple[Path, str]],
    manifest_path: Path,
    policy: GNNPolicy,
) -> Path:
    """Describe the complete bake-off and the one atomically activated winner."""
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_type": "graph_neural_network",
        "tenant_id": tenant_id,
        "model_version": model_version,
        "feature_schema_version": G.FEATURE_SCHEMA_VERSION,
        "graph_snapshot_id": graph_snapshot_id,
        "graph_snapshot_as_of": graph.get(
            "graph_snapshot_as_of", graph["t_graph"]
        ).isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "Tenant-scoped operational GNN architecture bake-off",
        "training_policy": policy.snapshot(),
        "selection": selection,
        "features": {
            "user": list(G.USER_FEATURE_COLUMNS),
            "nomination": list(G.NOMINATION_FEATURE_COLUMNS),
            "participant_roles": ["nominator", "beneficiary"],
            "behavior_statuses": ["Pending", "Approved", "Paid"],
        },
        "artifacts": [
            {
                **artifact_descriptor(path, role),
                "relative_path": path.relative_to(manifest_path.parent).as_posix(),
            }
            for path, role in artifact_paths
        ],
    }
    write_manifest(manifest_path, manifest)
    return manifest_path


# ── Per-tenant run ────────────────────────────────────────────────────────────

def _retain_labelled_targets(
    graph: dict,
    split: str,
    label_map: dict[int, int],
) -> np.ndarray:
    """Filter one target interval to independently human-confirmed outcomes."""
    target = graph[split]
    keep = [
        index
        for index, nomination_id in enumerate(target["nom_ids"])
        if nomination_id in label_map
    ]
    target["nom_ids"] = [target["nom_ids"][index] for index in keep]
    target["x"] = target["x"][keep]
    target["pairs"] = target["pairs"][keep]
    return np.asarray(
        [label_map[nomination_id] for nomination_id in target["nom_ids"]],
        dtype=np.int64,
    )


def _headline_candidate_metrics(metrics: dict) -> dict:
    return {
        key: metrics[key]
        for key in (
            "eval_pr_auc",
            "eval_roc_auc",
            "eval_base_rate",
            "eval_lift",
            "eval_brier_score",
            "training_duration_seconds",
            "holdout_inference_ms",
            "parameter_count",
            "n_train",
            "n_eval",
            "n_train_pos",
            "n_eval_pos",
            "epochs_run",
        )
    }


def _incumbent_selection(conn, tenant_id: int) -> dict | None:
    """Read the active architecture without making the status row a model registry."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DiagnosticsJson
        FROM dbo.IntegrityComponentStatus
        WHERE TenantId = ? AND Component = 'GNN'
    """, tenant_id)
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        import json

        diagnostics = json.loads(row[0])
        selection = diagnostics.get("selection") or {}
        value = selection.get("selected_architecture")
        return selection if value in GRAPH_ARCHITECTURES else None
    except (TypeError, ValueError):
        logger.warning(
            "Tenant %d has invalid GNN selection diagnostics; ignoring incumbent",
            tenant_id,
        )
        return None


def _candidate_training_set(
    folds: list[dict], y_train_by_fold: list[np.ndarray], y_holdout: np.ndarray
) -> tuple[list[dict], list[np.ndarray]]:
    """Add the untouched holdout as a final time-valid refit interval."""
    holdout_as_training = dict(folds[-1])
    holdout_as_training["train"] = folds[-1]["eval"]
    return [*folds, holdout_as_training], [*y_train_by_fold, y_holdout]


def _train_candidates(
    folds: list[dict],
    y_train_by_fold: list[np.ndarray],
    y_holdout: np.ndarray,
    policy: GNNPolicy,
) -> tuple[dict[str, object], dict[str, dict]]:
    """Run the standard MLP admission baseline and every configured graph model."""
    if policy.selection_metric != "holdout_pr_auc":
        raise ValueError(
            "The active GNN policy selection metric must be holdout_pr_auc"
        )
    invalid = sorted(
        set(policy.candidate_architectures) - set(GRAPH_ARCHITECTURES)
    )
    if invalid:
        raise ValueError(
            f"Unsupported GNN candidate(s): {invalid}; expected {GRAPH_ARCHITECTURES}"
        )
    architectures = ("mlp", *dict.fromkeys(policy.candidate_architectures))
    models: dict[str, object] = {}
    candidates: dict[str, dict] = {}
    for architecture in architectures:
        logger.info("GNN operational candidate starting: %s", architecture)
        try:
            model, metrics = train_candidate_rolling(
                folds,
                y_train_by_fold,
                y_holdout,
                architecture=architecture,
                hidden_dim=policy.hidden_dim,
                emb_dim=policy.embed_dim,
                epochs=policy.epochs,
            )
            models[architecture] = model
            candidates[architecture] = {
                "status": "COMPLETED",
                **_headline_candidate_metrics(metrics),
            }
        except Exception as exc:
            logger.exception("GNN candidate %s failed", architecture)
            candidates[architecture] = {
                "status": "FAILED",
                "reason": type(exc).__name__,
                "detail": str(exc)[:500],
            }
    return models, candidates

def _process_tenant(conn, tenant_id: int, run_id: str | None = None) -> str:
    t0 = time.monotonic()
    run_id = run_id or str(uuid.uuid4())
    policy = load_active_policy(conn, tenant_id)
    if policy is None:
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="NO_ACTIVE_POLICY",
            reason_detail="No active dbo.GNNScoringPolicies row exists for this tenant.",
            diagnostics={"gnn_policy_available": False},
            run_id=run_id,
        )
        return "SKIPPED (no active GNN scoring policy)"
    if not policy.training_enabled:
        incumbent_selection = _incumbent_selection(conn, tenant_id)
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="DISABLED",
            reason_code="DISABLED",
            reason_detail=(
                f"GNN training is disabled by active policy v{policy.policy_version}; "
                "the incumbent serving model was preserved."
            ),
            diagnostics={
                "gnn_training_enabled": False,
                "gnn_policy_id": policy.policy_id,
                "gnn_policy_version": policy.policy_version,
                "selection": incumbent_selection,
            },
            run_id=run_id,
        )
        return f"DISABLED (policy v{policy.policy_version})"

    logger.info(
        "Tenant %d GNN policy v%d: window %d days | folds %d | hidden %d | "
        "embedding %d | epochs %d | retention %d days",
        tenant_id, policy.policy_version, policy.window_days,
        policy.rolling_folds, policy.hidden_dim, policy.embed_dim,
        policy.epochs, policy.embedding_retention_days,
    )
    incumbent_selection = _incumbent_selection(conn, tenant_id)
    incumbent = (
        incumbent_selection.get("selected_architecture")
        if incumbent_selection else None
    )

    users, nominations = G.fetch_tenant_rows(conn, tenant_id, policy.window_days)
    behavior_nominations = [
        row for row in nominations
        if bool(row.get("IsBehaviorEligible", True))
    ]
    base_diagnostics = {
        "window_days": policy.window_days,
        "nomination_count": len(behavior_nominations),
        "user_count": len(users),
        "gnn_policy_id": policy.policy_id,
        "gnn_policy_version": policy.policy_version,
        **({"selection": incumbent_selection} if incumbent_selection else {}),
    }
    if (len(behavior_nominations) < policy.minimum_training_samples
            or len(users) < policy.minimum_users):
        detail = (f"{len(behavior_nominations)} nominations / {len(users)} users; "
                  f"requires {policy.minimum_training_samples} / {policy.minimum_users}")
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="BELOW_MINIMUM_VOLUME", reason_detail=detail,
            diagnostics={
                **base_diagnostics,
                "minimum_nominations": policy.minimum_training_samples,
                "minimum_users": policy.minimum_users,
            },
            run_id=run_id,
        )
        return (f"SKIPPED (below gate: {len(behavior_nominations)} nominations / {len(users)} users, "
                f"need {policy.minimum_training_samples}/{policy.minimum_users})")

    label_df = labels_mod.load_labels(conn, tenant_id, window_days=policy.window_days)
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

    try:
        G.rolling_thresholds(
            behavior_nominations,
            n_folds=policy.rolling_folds,
        )
    except ValueError as exc:
        upsert_component_status(
            conn,
            tenant_id=tenant_id,
            component="GNN",
            attempt_status="SKIPPED",
            reason_code="INSUFFICIENT_TEMPORAL_COVERAGE",
            reason_detail=str(exc),
            diagnostics={
                **base_diagnostics,
                "rolling_fold_count": policy.rolling_folds,
            },
            run_id=run_id,
        )
        return f"SKIPPED (insufficient temporal coverage: {exc})"
    # Isolation and graph-construction failures are not ordinary data-volume
    # skips. Let them fail the tenant run visibly rather than misclassifying a
    # possible cross-tenant reference as insufficient temporal coverage.
    folds = G.build_rolling_folds(
        users,
        nominations,
        n_folds=policy.rolling_folds,
    )

    y_train_by_fold = [
        _retain_labelled_targets(fold, "train", label_map) for fold in folds
    ]
    y_ev = _retain_labelled_targets(folds[-1], "eval", label_map)
    y_tr = np.concatenate(y_train_by_fold)
    holdout_graph = folds[-1]

    train_pos = int(y_tr.sum())
    eval_pos = int(y_ev.sum())
    train_neg = int(len(y_tr) - train_pos)
    eval_neg = int(len(y_ev) - eval_pos)
    if (train_pos < policy.minimum_positives_per_split
            or eval_pos < policy.minimum_positives_per_split):
        detail = (
            f"rolling-train fraud labels {train_pos}, final-holdout fraud labels "
            f"{eval_pos}; requires {policy.minimum_positives_per_split} in each population"
        )
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="INSUFFICIENT_FRAUD_LABELS", reason_detail=detail,
            diagnostics={
                **base_diagnostics,
                "train_positive_count": train_pos, "eval_positive_count": eval_pos,
                "minimum_positives_per_split": policy.minimum_positives_per_split,
                "train_negative_count": train_neg, "eval_negative_count": eval_neg,
                "rolling_fold_count": len(folds),
                "fold_training_counts": [len(labels) for labels in y_train_by_fold],
            },
            run_id=run_id,
        )
        return (f"SKIPPED (too few human-confirmed fraud labels: train {train_pos}, "
                f"eval {eval_pos}, need {policy.minimum_positives_per_split} each)")
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
                "rolling_fold_count": len(folds),
                "fold_training_counts": [len(labels) for labels in y_train_by_fold],
            },
            run_id=run_id,
        )
        return (f"SKIPPED (human-confirmed labels need both classes: "
                f"train {train_pos} fraud/{train_neg} legitimate, "
                f"eval {eval_pos} fraud/{eval_neg} legitimate)")

    candidate_models, candidate_metrics = _train_candidates(
        folds, y_train_by_fold, y_ev, policy
    )
    selection = select_architecture(
        candidate_metrics,
        incumbent_architecture=incumbent,
        minimum_improvement_over_mlp=policy.minimum_improvement_over_mlp,
        incumbent_tie_tolerance=policy.incumbent_tie_tolerance,
        minimum_eligible_graph_candidates=policy.minimum_eligible_graph_candidates,
    )
    candidate_metrics = selection["candidates"]

    # Candidate evaluation remains tied to the untouched holdout. Only after
    # selection is final do we admit that matured interval to a fresh refit.
    graph = G.build_serving_graph(users, nominations)
    as_of = date.today()
    run_suffix = run_id.replace("-", "")[:8]
    model_version = f"gnn-v2-{as_of:%Y%m%d}-t{tenant_id}-{run_suffix}"
    graph_snapshot_id = f"gnn-graph-v2-{as_of:%Y%m%d}-t{tenant_id}-{run_suffix}"
    selection["model_version"] = model_version
    selection["selected_at"] = datetime.now(timezone.utc).isoformat()

    bundle_dir = OUTPUT_DIR / "gnn" / f"tenant_{tenant_id}" / model_version
    bundle_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = bundle_dir / "graph_snapshot.pt"
    manifest_path = bundle_dir / "manifest.json"
    snapshot = bundle.build_snapshot(
        graph=graph,
        tenant_id=tenant_id,
        model_version=model_version,
        graph_snapshot_id=graph_snapshot_id,
    )
    bundle.write_snapshot(snapshot_path, snapshot)
    artifact_paths: list[tuple[Path, str]] = [
        (snapshot_path, "explanation_graph_snapshot")
    ]
    for architecture, model in candidate_models.items():
        candidate_dir = bundle_dir / "candidates" / architecture
        candidate_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = candidate_dir / "metrics.json"
        write_manifest(metrics_path, candidate_metrics[architecture])
        artifact_paths.append((metrics_path, f"candidate_{architecture}_metrics"))
        decoder_path = candidate_dir / "decoder.pt"
        _write_head(
            model, graph, model_version, graph_snapshot_id,
            candidate_metrics[architecture], decoder_path, policy,
        )
        artifact_paths.append((decoder_path, f"candidate_{architecture}_decoder"))
        if architecture in GRAPH_ARCHITECTURES:
            encoder_path = candidate_dir / "encoder.pt"
            _write_encoder(
                model, graph, model_version, graph_snapshot_id, encoder_path,
                policy,
            )
            artifact_paths.append(
                (encoder_path, f"candidate_{architecture}_encoder")
            )

    selected_architecture = selection["selected_architecture"]
    serving_model = None
    serving_metrics = None
    if selected_architecture:
        refit_folds, refit_labels = _candidate_training_set(
            folds, y_train_by_fold, y_ev
        )
        serving_model, serving_metrics = fit_candidate_rolling(
            refit_folds,
            refit_labels,
            architecture=selected_architecture,
            hidden_dim=policy.hidden_dim,
            emb_dim=policy.embed_dim,
            epochs=policy.epochs,
        )
        serving_dir = bundle_dir / "serving"
        serving_dir.mkdir(parents=True, exist_ok=True)
        serving_encoder = serving_dir / "encoder.pt"
        serving_decoder = serving_dir / "decoder.pt"
        _write_encoder(
            serving_model, graph, model_version, graph_snapshot_id,
            serving_encoder, policy,
        )
        _write_head(
            serving_model, graph, model_version, graph_snapshot_id,
            {**candidate_metrics[selected_architecture], "refit": serving_metrics},
            serving_decoder, policy,
        )
        artifact_paths.extend([
            (serving_encoder, "serving_encoder"),
            (serving_decoder, "serving_decoder"),
        ])

    _write_operational_manifest(
        tenant_id=tenant_id,
        graph=graph,
        model_version=model_version,
        graph_snapshot_id=graph_snapshot_id,
        selection=selection,
        artifact_paths=artifact_paths,
        manifest_path=manifest_path,
        policy=policy,
    )
    artifact_paths.append((manifest_path, "operational_manifest"))

    # Upload the whole immutable run before the SQL serving pointer changes.
    versioned_folder = f"gnn/tenant_{tenant_id}/{model_version}"
    bundle_uploads = []
    for artifact, _role in artifact_paths:
        relative_parent = artifact.relative_to(bundle_dir).parent.as_posix()
        blob_folder = (
            versioned_folder
            if relative_parent == "."
            else f"{versioned_folder}/{relative_parent}"
        )
        bundle_uploads.append(
            _upload_artefact(artifact, blob_folder=blob_folder)
        )
    if os.getenv("AZURE_STORAGE_ACCOUNT") and not all(bundle_uploads):
        raise RuntimeError(
            "GNN candidate bundle upload was incomplete; serving pointer was preserved"
        )

    common_diagnostics = {
        **base_diagnostics,
        "human_confirmed_eval_count": len(y_ev),
        "human_confirmed_train_count": len(y_tr),
        "rolling_fold_count": len(folds),
        "holdout_start": holdout_graph["t_cut"].isoformat(),
        "holdout_end": holdout_graph["eval_end"].isoformat(),
        "selection": selection,
        "feature_schema_version": graph["feature_schema_version"],
        "graph_snapshot_id": graph_snapshot_id,
        "graph_snapshot_as_of": graph.get(
            "graph_snapshot_as_of", graph["t_graph"]
        ).isoformat(),
        "artifact_bundle_prefix": versioned_folder,
    }
    if serving_model is None:
        reason = selection["selection_reason"]
        skipped_diagnostics = {
            **common_diagnostics,
            "selection": incumbent_selection or selection,
            "last_candidate_selection": selection,
        }
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code=reason,
            reason_detail=(
                "The candidate run did not produce an eligible graph architecture; "
                "the current serving model was preserved."
            ),
            diagnostics=skipped_diagnostics,
            run_id=run_id,
        )
        return (
            f"SKIPPED ({reason}; incumbent {incumbent or 'none'} preserved; "
            f"{time.monotonic() - t0:.1f}s)"
        )

    with torch.no_grad():
        z = serving_model.embed_users(graph["data"]).numpy().astype(np.float32)
        user_ids = sorted(
            graph["user_index"], key=lambda user_id: graph["user_index"][user_id]
        )
    n_emb = _publish_embeddings(
        conn, tenant_id, user_ids, z, as_of, model_version
    )
    n_evicted = _evict_stale_embeddings(
        conn, tenant_id, policy.embedding_retention_days
    )

    # This upsert is the activation pointer and therefore happens last.
    upsert_component_status(
        conn, tenant_id=tenant_id, component="GNN", attempt_status="SUCCEEDED",
        serving_status="AVAILABLE",
        serving_version=model_version,
        serving_as_of=as_of,
        run_id=run_id,
        diagnostics={
            **common_diagnostics,
            "embedding_count": n_emb,
            "evicted_embedding_count": n_evicted,
            "human_label_pr_auc": selection["selected_metric_value"],
            "serving_refit_training_count": serving_metrics["n_train"],
        },
    )

    return (f"OK ({model_version}, {selected_architecture}, {n_emb} embeddings, "
            f"{n_evicted} evicted, human-label PR-AUC "
            f"{selection['selected_metric_value']:.4f}, "
            f"{time.monotonic() - t0:.1f}s)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main(tenants_to_process: list | None = None) -> None:
    """Called by run_job.py. Signature matches every other stage."""
    run_id = str(uuid.uuid4())
    logger.info("GNN MODEL TRAINING - Multi-Tenant")
    logger.info(
        "Each tenant's active dbo.GNNScoringPolicies row is read immediately "
        "before that tenant is processed."
    )

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
