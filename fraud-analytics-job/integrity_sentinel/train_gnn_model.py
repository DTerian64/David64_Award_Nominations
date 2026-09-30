"""
train_gnn_model.py — GNN training stage
=========================================
Stage 3 of the fraud-analytics-job pipeline, registered in run_job.py STAGES
after train_tabular_model.

Per tenant:
    1. Load one canonical source snapshot and Sentinel-owned outcomes.
    2. Build the per-tenant heterogeneous graph from canonical records.
    3. Compare the MLP admission baseline and configured graph architectures.
    4. Select one graph winner by the versioned operational policy.
    5. Refit the winner over all matured labels and publish its embeddings.
    6. Upload the immutable candidate and serving bundle.
    7. Activate it through integrity.IntegrityComponentStatus as the final step.

Ordering rationale
------------------
Runs after train_tabular_model for stable operations. Both models independently read
the same human label contract, and a GNN failure cannot block the RF retrain — the
per-stage try/except in run_job.run_stage() provides that isolation. The cost is
that sync_holidays and forecast_models run later in the weekly window.

No post-hook. The backend does not load model artifacts, so
/api/internal/refresh-fraud-model is not called; integrity-check streams the
decoder itself on first use per tenant.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import torch
from integrity_engine.artifact_paths import gnn_bundle_prefix

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

from modeling.gnn import graph as G  # noqa: E402 - .env must load before model imports
from modeling.gnn import artifact_bundle as bundle  # noqa: E402
from modeling import labels as labels_mod  # noqa: E402
from modeling.artifact_manifest import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    artifact_descriptor,
    write_manifest,
)
from integrity_sentinel.component_status import upsert_component_status  # noqa: E402
from integrity_sentinel.db import RenewableConnection, connect  # noqa: E402
from integrity_sentinel.analytics_coordinator import LeaseLostError  # noqa: E402
from integrity_sentinel.datasets import load_award_nomination_dataset  # noqa: E402
from feature_builders.source_views import (  # noqa: E402
    actor_rows,
    gnn_nomination_rows,
    label_frame,
)
from source_adapters.award_nominations.connection import (  # noqa: E402
    connect as connect_award,
)
from source_adapters.award_nominations.tenant_config import (  # noqa: E402
    get_tenant_gnn_window,
    get_tenants as get_enabled_tenants,
)
from source_adapters.contracts import SourceReadRequest  # noqa: E402
from utils.stage_result import TenantStageResult  # noqa: E402
from modeling.gnn.model import (  # noqa: E402
    _RELATIONS,
    fit_candidate_rolling,
    train_candidate_rolling,
)
from modeling.gnn.evaluators.selection_by_holdout_pr_auc import (  # noqa: E402
    GRAPH_ARCHITECTURES,
    select_architecture,
)
from modeling.gnn.evaluators.graph_value_by_ablation import (  # noqa: E402
    evaluate_graph_value,
)
from integrity_sentinel.gnn_policy import GNNPolicy, load_active_policy  # noqa: E402
from modeling.gnn.specialists.contracts import SERVING_MODE_SPECIALISTS  # noqa: E402
from modeling.gnn.specialists.contracts import SERVING_MODE_SHARED_MULTI_HEAD  # noqa: E402
from modeling.gnn.evaluators.selection_by_temporal_validation.evaluator import (  # noqa: E402
    evaluate_shared_model,
)
from modeling.gnn.specialists.evaluator import (  # noqa: E402
    evaluate_specialists,
    specialist_fold_views,
)
from modeling.gnn.specialists.feature_contracts import (  # noqa: E402
    apply_specialist_feature_contract,
)
from modeling.gnn.specialists.labels import build_specialist_label_maps  # noqa: E402

# Reuse the Random Forest's blob upload helper rather than duplicating the auth
# and error handling. Both stages run in the same process under run_job.py.
from utils.model_artifacts import upload_artifact  # noqa: E402

logger = logging.getLogger(__name__)

OUTPUT_DIR = JOB_DIR / "Output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_PUBLICATION_MAX_ATTEMPTS = 3
_PUBLICATION_RETRY_DELAY_SECONDS = 2.0
_TRANSIENT_SQL_MARKERS = (
    "08S01", "08001", "08003", "08006", "08007", "40001",
    "40197", "40501", "40613", "49918", "49919", "49920",
)


def _is_transient_sql_error(exc: Exception) -> bool:
    text = " ".join(str(value) for value in getattr(exc, "args", (exc,)))
    return any(marker in text for marker in _TRANSIENT_SQL_MARKERS)


def _run_publication_with_retry(
    connection: RenewableConnection,
    *,
    tenant_id: int,
    operation_name: str,
    operation: Callable[[], object],
    reconcile: Callable[[], tuple[bool, object]] | None = None,
):
    """Run an idempotent publication transaction with bounded reconnects.

    The operation must make its activation/status upsert last so that its commit
    atomically covers all earlier writes. Reconciliation handles the ambiguous
    case where the server committed but the acknowledgement was lost.
    """
    for attempt in range(1, _PUBLICATION_MAX_ATTEMPTS + 1):
        started = time.monotonic()
        logger.info(
            "Tenant %d: GNN publication started operation=%s attempt=%d/%d",
            tenant_id,
            operation_name,
            attempt,
            _PUBLICATION_MAX_ATTEMPTS,
        )
        try:
            result = operation()
            logger.info(
                "Tenant %d: GNN publication completed operation=%s attempt=%d duration=%.1fs",
                tenant_id,
                operation_name,
                attempt,
                time.monotonic() - started,
            )
            return result
        except LeaseLostError:
            connection.discard()
            raise
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                logger.warning(
                    "Tenant %d: publication rollback failed; discarding connection",
                    tenant_id,
                    exc_info=True,
                )
            connection.discard()
            if not _is_transient_sql_error(exc):
                raise

            if reconcile is not None:
                try:
                    matched, reconciled_result = reconcile()
                    if matched:
                        logger.warning(
                            "Tenant %d: GNN publication commit reconciled after transient error "
                            "operation=%s attempt=%d",
                            tenant_id,
                            operation_name,
                            attempt,
                        )
                        return reconciled_result
                except Exception:
                    logger.warning(
                        "Tenant %d: GNN publication reconciliation failed; retrying",
                        tenant_id,
                        exc_info=True,
                    )
                    connection.discard()

            if attempt == _PUBLICATION_MAX_ATTEMPTS:
                raise
            delay = _PUBLICATION_RETRY_DELAY_SECONDS * attempt
            logger.warning(
                "Tenant %d: transient SQL publication failure operation=%s "
                "attempt=%d/%d; retrying on a fresh connection in %.1fs: %s",
                tenant_id,
                operation_name,
                attempt,
                _PUBLICATION_MAX_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)
    raise AssertionError("publication retry loop exhausted unexpectedly")


def _reconcile_gnn_status(
    connection: RenewableConnection,
    *,
    tenant_id: int,
    run_id: str,
    expected_status: str,
    expected_version: str | None = None,
) -> tuple[bool, dict]:
    """Check whether an activation commit succeeded before its ACK was lost."""
    row = connection.cursor().execute(
        """
        SELECT LastAttemptStatus, ServingVersion, DiagnosticsJson
        FROM integrity.IntegrityComponentStatus
        WHERE TenantId=? AND Component='GNN' AND RunId=?
        """,
        (tenant_id, run_id),
    ).fetchone()
    connection.rollback()
    if row is None or str(row[0]).upper() != expected_status.upper():
        return False, {}
    if expected_version is not None and str(row[1] or "") != expected_version:
        return False, {}
    diagnostics = json.loads(row[2]) if row[2] else {}
    return True, diagnostics


def _read_gnn_outcome_with_retry(
    connection: RenewableConnection,
    tenant_id: int,
):
    """Read the committed stage outcome without turning a transient into failure."""
    for attempt in range(1, _PUBLICATION_MAX_ATTEMPTS + 1):
        try:
            row = connection.cursor().execute(
                """
                SELECT LastAttemptStatus, ReasonCode, ServingVersion
                FROM integrity.IntegrityComponentStatus
                WHERE TenantId=? AND Component='GNN'
                """,
                (tenant_id,),
            ).fetchone()
            connection.rollback()
            return row
        except Exception as exc:
            connection.discard()
            if (
                not _is_transient_sql_error(exc)
                or attempt == _PUBLICATION_MAX_ATTEMPTS
            ):
                raise
            delay = _PUBLICATION_RETRY_DELAY_SECONDS * attempt
            logger.warning(
                "Tenant %d: transient SQL outcome-read failure attempt=%d/%d; "
                "retrying on a fresh connection in %.1fs: %s",
                tenant_id,
                attempt,
                _PUBLICATION_MAX_ATTEMPTS,
                delay,
                exc,
            )
            time.sleep(delay)
    raise AssertionError("outcome read retry loop exhausted unexpectedly")


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
    return [tenant_id for tenant_id, _ in get_enabled_tenants(conn)]


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
        MERGE integrity.GNN_UserEmbeddings AS target
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
        "DELETE FROM integrity.GNN_UserEmbeddings WHERE TenantId = ? AND AsOfDate < ?",
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
    specialist_key: str | None = None,
    calibration: dict | None = None,
    pattern_head_states: dict | None = None,
) -> None:
    """
    Serialise the decoder and its training-fitted preprocessing.
    Live v4 inference also loads the matching frozen encoder and manifest.

    Every value here must be a torch tensor or a Python primitive. gnn_check.py
    loads with torch.load(weights_only=True), which rejects numpy's array
    reconstructor, so scalers are lists of floats rather than ndarrays. That
    restriction is what stops a .pt file being as executable as a .pkl — do not
    add a richer object here to save a conversion.
    """
    multi_head = hasattr(model.decoder, "overall")
    head = {
        "decoder_state_dict":         (
            model.decoder.overall.net.state_dict() if multi_head
            else model.decoder.net.state_dict()
        ),
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
        "behavior_statuses":          list(G.BEHAVIOR_STATUSES),
        "nomination_feature_columns": list(graph["nomination_feature_columns"]),
        "causal_context_window_days": int(graph["causal_context_window_days"]),
        "nomination_scaler_mean":     [float(v) for v in graph["nomination_scaler"]["mean"]],
        "nomination_scaler_std":      [float(v) for v in graph["nomination_scaler"]["std"]],
        # Applied to raw user inputs by live graph assembly, never to the
        # encoder-output embeddings consumed by the decoder. Legacy bundles
        # retain these for reproducibility only.
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
    if graph.get("specialist_feature_contract"):
        head["specialist_feature_contract"] = graph[
            "specialist_feature_contract"
        ]
    if specialist_key:
        head["specialist_key"] = specialist_key
    if calibration:
        head["calibration"] = calibration
    if multi_head:
        head["model_schema_version"] = 4
        head["inference_contract"] = graph["inference_contract"]
        head["tenant_id"] = graph["tenant_id"]
        head["user_feature_columns"] = list(graph["user_feature_columns"])
        head["pattern_head_states"] = pattern_head_states or {}
        head["pattern_feature_contracts"] = {
            key: contract for key, contract, enabled in policy.pattern_heads if enabled
        }
        head["pattern_decoder_state_dicts"] = {
            key: model.decoder.patterns[key].net.state_dict()
            for key, state in (pattern_head_states or {}).items()
            if state.get("state") == "ACTIVE"
            and key in model.decoder.patterns
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
        **({"tenant_id": graph["tenant_id"], "inference_contract": graph["inference_contract"]}
           if graph.get("inference_contract") else {}),
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
    graph_value_evaluation: dict,
    specialist_evaluation: dict | None,
    specialist_serving: dict | None,
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
        "graph_value_evaluation": graph_value_evaluation,
        "specialist_evaluation": specialist_evaluation,
        "specialists": specialist_serving,
        "features": {
            "user": list(G.USER_FEATURE_COLUMNS),
            "nomination": list(G.NOMINATION_FEATURE_COLUMNS),
            "causal_context_window_days": int(
                graph["causal_context_window_days"]
            ),
            "participant_roles": ["nominator", "beneficiary"],
            "behavior_statuses": list(G.BEHAVIOR_STATUSES),
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
        FROM integrity.IntegrityComponentStatus
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


def _incumbent_specialists(conn, tenant_id: int) -> dict[str, dict]:
    """Read the active specialist roster for refresh or carry-forward."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ServingVersion, DiagnosticsJson
        FROM integrity.IntegrityComponentStatus
        WHERE TenantId = ? AND Component = 'GNN'
    """, tenant_id)
    row = cur.fetchone()
    if not row or not row[1]:
        return {}
    try:
        import json

        diagnostics = json.loads(row[1])
        roster = diagnostics.get("specialists") or {}
        if not isinstance(roster, dict):
            return {}
        result = {}
        for key, value in roster.items():
            if not isinstance(value, dict) or value.get("state") not in {
                "ACTIVE", "CARRIED_FORWARD"
            }:
                continue
            result[str(key).upper()] = {
                **value,
                "artifact_bundle_version": value.get(
                    "artifact_bundle_version", row[0]
                ),
            }
        return result
    except (TypeError, ValueError):
        logger.warning(
            "Tenant %d has invalid GNN specialist diagnostics; ignoring incumbents",
            tenant_id,
        )
        return {}
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


def _fit_admitted_specialists(
    *,
    policy: GNNPolicy,
    folds: list[dict],
    label_maps: dict[str, dict[int, int]],
    evaluation: dict,
    serving_graph: dict,
    tenant_id: int,
    bundle_version: str,
    graph_snapshot_id: str,
    run_suffix: str,
    bundle_dir: Path,
    incumbents: dict[str, dict],
) -> tuple[dict[str, tuple[object, str]], dict, list[tuple[Path, str]]]:
    """Refit admitted tracks and write their immutable serving artifacts."""
    policies = {track.key: track for track in policy.specialist_tracks}
    models: dict[str, tuple[object, str]] = {}
    serving: dict[str, dict] = {}
    artifacts: list[tuple[Path, str]] = []
    for key, result in evaluation["tracks"].items():
        if result.get("status") != "ADMITTED":
            incumbent = incumbents.get(key)
            if incumbent:
                serving[key] = {
                    **incumbent,
                    "state": "CARRIED_FORWARD",
                    "carry_forward_reason": result.get("reason"),
                }
                continue
            serving[key] = {
                "state": "DISABLED" if result.get("status") == "DISABLED" else "NOT_ADMITTED",
                "reason": result.get("reason"),
                "feature_contract": result.get("feature_contract"),
            }
            continue
        track = policies[key]
        architecture = result["provisional_architecture"]
        views, y_train, y_eval = specialist_fold_views(
            folds, label_maps[key], track.feature_contract
        )
        refit_folds, refit_labels = _candidate_training_set(
            views, y_train, y_eval[-1]
        )
        model, refit_metrics = fit_candidate_rolling(
            refit_folds,
            refit_labels,
            architecture=architecture,
            hidden_dim=policy.hidden_dim,
            emb_dim=policy.embed_dim,
            epochs=policy.epochs,
        )
        slug = key.lower().replace("_", "-")
        specialist_version = (
            f"g3-t{tenant_id}-{slug}-{architecture}-{run_suffix}"
        )
        profiled_graph = apply_specialist_feature_contract(
            serving_graph, track.feature_contract
        )
        serving_dir = bundle_dir / "specialists" / key.lower() / "serving"
        serving_dir.mkdir(parents=True, exist_ok=True)
        encoder_path = serving_dir / "encoder.pt"
        decoder_path = serving_dir / "decoder.pt"
        metrics_path = serving_dir / "metrics.json"
        _write_encoder(
            model,
            profiled_graph,
            specialist_version,
            graph_snapshot_id,
            encoder_path,
            policy,
        )
        _write_head(
            model,
            profiled_graph,
            specialist_version,
            graph_snapshot_id,
            {
                "admission": result,
                "refit": refit_metrics,
                "bundle_version": bundle_version,
            },
            decoder_path,
            policy,
            specialist_key=key,
            calibration=(
                result["candidates"][architecture].get("calibration")
            ),
        )
        metrics = {
            "schema_version": 1,
            "track": key,
            "state": "ACTIVE",
            "architecture": architecture,
            "model_version": specialist_version,
            "artifact_bundle_version": bundle_version,
            "bundle_version": bundle_version,
            "feature_contract": track.feature_contract,
            "admission": result,
            "refit": refit_metrics,
        }
        write_manifest(metrics_path, metrics)
        artifacts.extend([
            (encoder_path, f"specialist_{key.lower()}_serving_encoder"),
            (decoder_path, f"specialist_{key.lower()}_serving_decoder"),
            (metrics_path, f"specialist_{key.lower()}_serving_metrics"),
        ])
        models[key] = (model, specialist_version)
        serving[key] = {
            "state": "ACTIVE",
            "architecture": architecture,
            "model_version": specialist_version,
            "feature_contract": track.feature_contract,
            "decoder_relative_path": (
                f"specialists/{key.lower()}/serving/decoder.pt"
            ),
            "encoder_relative_path": (
                f"specialists/{key.lower()}/serving/encoder.pt"
            ),
            "admission_reason": result["reason"],
            "final_holdout_pr_auc": result.get("final_holdout_pr_auc"),
            "final_holdout_mlp_pr_auc": result.get(
                "final_holdout_mlp_pr_auc"
            ),
        }
    return models, serving, artifacts


def _shared_admission_failure(
    final: dict,
    selected: str | None,
    policy: GNNPolicy,
) -> tuple[str, str]:
    """Return the stable reason code and detail for a rejected shared model."""
    if not selected:
        return "NO_VALIDATION_CANDIDATE", "No GNN architecture completed validation."

    graph_value = final.get("graph_value_over_raw_mlp")
    if (
        graph_value is not None
        and graph_value < policy.minimum_graph_value_over_raw_mlp
    ):
        return (
            "INSUFFICIENT_GRAPH_VALUE_OVER_RAW_MLP",
            "V4 final temporal test graph value over raw-feature MLP was "
            f"{graph_value:.6f}; policy requires at least "
            f"{policy.minimum_graph_value_over_raw_mlp:.6f}. "
            "The incumbent was preserved.",
        )

    return (
        "NO_MESSAGE_PASSING_VALUE_OVER_BASELINES",
        "V4 final temporal test did not pass the remaining admission checks; "
        "incumbent preserved.",
    )


def _process_shared_multi_head(
    conn, *, tenant_id: int, run_id: str, policy: GNNPolicy,
    users: list, nominations: list, labelled, folds: list[dict],
    base_diagnostics: dict, label_source_counts: dict, started: float,
    data_as_of_utc: datetime,
    lease_guard: Callable[[], None] | None = None,
    lease_fence: Callable[[object], None] | None = None,
) -> str:
    """Publish one v4 bundle, preserving the incumbent on failed admission."""
    evaluation_started = time.monotonic()
    logger.info(
        "Tenant %d: shared multi-head GNN evaluation started candidates=%s folds=%d",
        tenant_id,
        list(policy.candidate_architectures),
        len(folds),
    )
    report, selected_model = evaluate_shared_model(folds, labelled, policy)
    final = report.get("final_test") or {}
    selected = report["selection"].get("selected_architecture")
    admitted = bool(final.get("admitted") and selected_model is not None)
    logger.info(
        "Tenant %d: shared multi-head GNN evaluation completed selected=%s "
        "admitted=%s duration=%.1fs",
        tenant_id,
        selected,
        admitted,
        time.monotonic() - evaluation_started,
    )
    graph = G.build_serving_graph(
        users, nominations, causal_window_days=policy.window_days,
    )
    from integrity_engine.gnn.live_graph import (
        LIVE_ENCODING_CONTRACT, build_live_graph_inputs, preprocessing_from_graph,
    )
    # The selected decoder/encoder were fitted on fold scalers. Refitting these
    # on the publication graph would silently change the model's input space.
    for key in ("user_scaler", "nomination_scaler", "category_amount_stats"):
        graph[key] = folds[-1][key]
    graph["tenant_id"] = tenant_id
    graph["inference_contract"] = LIVE_ENCODING_CONTRACT
    publication_target = {
        "NominationId": max(int(row["NominationId"]) for row in nominations) + 1,
        "CreatedAt": graph["t_graph"],
        "NominatorId": users[0]["UserId"], "BeneficiaryId": users[0]["UserId"],
    }
    publication_inputs = build_live_graph_inputs(
        users, nominations, publication_target, preprocessing_from_graph(graph),
    )
    for node_type, values in publication_inputs.x_dict.items():
        graph["data"][node_type].x = values
    for relation, values in publication_inputs.edge_index_dict.items():
        graph["data"][relation].edge_index = values
    graph["user_index"] = publication_inputs.user_index
    graph["category_index"] = publication_inputs.category_index
    graph["graph_nomination_ids"] = publication_inputs.nomination_ids
    as_of = data_as_of_utc.date()
    suffix = run_id.replace("-", "")[:8]
    model_version = f"gnn-v4-{as_of:%Y%m%d}-t{tenant_id}-{suffix}"
    graph_snapshot_id = f"gnn-graph-v4-{as_of:%Y%m%d}-t{tenant_id}-{suffix}"
    bundle_dir = OUTPUT_DIR / "gnn" / f"tenant_{tenant_id}" / model_version
    bundle_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = bundle_dir / "graph_snapshot.pt"
    bundle.write_snapshot(snapshot_path, bundle.build_snapshot(
        graph=graph, tenant_id=tenant_id, model_version=model_version,
        graph_snapshot_id=graph_snapshot_id,
    ))
    artifacts: list[tuple[Path, str]] = [(snapshot_path, "explanation_graph_snapshot")]
    for architecture, candidate in report["validation_candidates"].items():
        candidate_path = bundle_dir / "candidates" / architecture / "metrics.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        write_manifest(candidate_path, candidate)
        artifacts.append((candidate_path, f"candidate_{architecture}_metrics"))
    if final:
        for architecture, metrics in final["models"].items():
            metrics_path = bundle_dir / "candidates" / architecture / "final_test_metrics.json"
            metrics_path.parent.mkdir(parents=True, exist_ok=True)
            write_manifest(metrics_path, metrics)
            artifacts.append((metrics_path, f"candidate_{architecture}_final_test"))
    if admitted:
        serving_dir = bundle_dir / "serving"
        serving_dir.mkdir(parents=True, exist_ok=True)
        encoder_path = serving_dir / "encoder.pt"
        decoder_path = serving_dir / "decoder.pt"
        _write_encoder(
            selected_model, graph, model_version, graph_snapshot_id,
            encoder_path, policy,
        )
        _write_head(
            selected_model, graph, model_version, graph_snapshot_id,
            {"final_test_pr_auc": final["models"][selected]["overall"]["pr_auc"]},
            decoder_path, policy, pattern_head_states=final["head_states"],
            calibration=final["calibration"],
        )
        artifacts.extend([
            (encoder_path, "serving_encoder"),
            (decoder_path, "serving_decoder"),
        ])
    manifest_path = bundle_dir / "manifest.json"
    write_manifest(manifest_path, {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_type": "graph_neural_network",
        "model_schema_version": 4,
        "inference_contract": LIVE_ENCODING_CONTRACT,
        "tenant_id": tenant_id,
        "model_version": model_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_as_of_utc": data_as_of_utc.isoformat(),
        "graph_snapshot_id": graph_snapshot_id,
        "graph_snapshot_as_of": graph.get(
            "graph_snapshot_as_of", graph["t_graph"]
        ).isoformat(),
        "feature_schema_version": graph["feature_schema_version"],
        "training_policy": policy.snapshot(),
        "selection": report["selection"],
        "shared_multi_head_evaluation": report,
        "artifacts": [{
            **artifact_descriptor(path, role),
            "relative_path": path.relative_to(bundle_dir).as_posix(),
        } for path, role in artifacts],
    })
    artifacts.append((manifest_path, "operational_manifest"))
    prefix = gnn_bundle_prefix(tenant_id, model_version)
    uploaded = []
    upload_started = time.monotonic()
    logger.info(
        "Tenant %d: GNN artifact upload started files=%d prefix=%s",
        tenant_id,
        len(artifacts),
        prefix,
    )
    for path, _role in artifacts:
        relative_parent = path.relative_to(bundle_dir).parent.as_posix()
        folder = prefix if relative_parent == "." else f"{prefix}/{relative_parent}"
        uploaded.append(upload_artifact(path, blob_folder=folder))
    logger.info(
        "Tenant %d: GNN artifact upload completed files=%d duration=%.1fs",
        tenant_id,
        len(artifacts),
        time.monotonic() - upload_started,
    )
    if os.getenv("AZURE_STORAGE_ACCOUNT") and not all(uploaded):
        raise RuntimeError("GNN v4 bundle upload incomplete; incumbent preserved")

    selected_metrics = (final.get("models") or {}).get(selected, {})
    diagnostics = {
        **{key: value for key, value in base_diagnostics.items() if key != "selection"},
        "diagnostics_schema_version": 4,
        "model_version": model_version,
        "serving_mode": SERVING_MODE_SHARED_MULTI_HEAD,
        "inference_contract": LIVE_ENCODING_CONTRACT,
        "selected_architecture": selected,
        "selection_reason": report["selection"].get("reason"),
        "validation_overall_pr_auc": report["selection"].get(
            "validation_overall_pr_auc"
        ),
        "final_test_overall": selected_metrics.get("overall"),
        "raw_mlp_overall": (
            (final.get("models") or {}).get("raw_feature_mlp") or {}
        ).get("overall"),
        "engineered_graph_mlp_overall": (
            (final.get("models") or {}).get("engineered_graph_mlp") or {}
        ).get("overall"),
        "head_states": {
            key: {
                "state": value["state"],
                "training_positive_count": value.get("training_positive_count"),
                "final_test_positive_count": (
                    value.get("final_test") or {}
                ).get("positive_count"),
                "final_test_pr_auc": (
                    value.get("final_test") or {}
                ).get("pr_auc"),
            }
            for key, value in (final.get("head_states") or {}).items()
        },
        "label_source_counts": label_source_counts,
        "artifact_bundle_prefix": prefix,
        "graph_snapshot_id": graph_snapshot_id,
        "admitted": admitted,
        "graph_value_over_raw_mlp": final.get("graph_value_over_raw_mlp"),
        "required_graph_value_over_raw_mlp": (
            policy.minimum_graph_value_over_raw_mlp
        ),
        "message_passing_value_over_engineered_graph_mlp": final.get(
            "message_passing_value_over_engineered_graph_mlp"
        ),
        "required_message_passing_value_over_engineered_graph_mlp": (
            policy.minimum_message_passing_value_over_engineered_graph_mlp
        ),
        "admission_checks": final.get("admission_checks"),
    }
    if not admitted:
        reason, reason_detail = _shared_admission_failure(
            final, selected, policy
        )

        def publish_skip():
            _guard_lease(lease_guard)
            _fence_lease(conn, lease_fence)
            upsert_component_status(
                conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
                reason_code=reason,
                reason_detail=reason_detail,
                diagnostics=diagnostics, run_id=run_id,
            )

        def reconcile_skip():
            matched, _diagnostics = _reconcile_gnn_status(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                expected_status="SKIPPED",
            )
            return matched, None

        _run_publication_with_retry(
            conn,
            tenant_id=tenant_id,
            operation_name="activate-skip",
            operation=publish_skip,
            reconcile=reconcile_skip,
        )
        return f"SKIPPED ({reason}; v4 manifest {model_version}; {time.monotonic() - started:.1f}s)"

    user_ids = sorted(graph["user_index"], key=graph["user_index"].get)
    with torch.no_grad():
        embeddings = selected_model.embed_users(graph["data"]).numpy().astype(np.float32)

    def publish_success():
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
        count = _publish_embeddings(
            conn, tenant_id, user_ids, embeddings, as_of, model_version
        )
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
        evicted = _evict_stale_embeddings(
            conn, tenant_id, policy.embedding_retention_days
        )
        # Only this last write makes the complete uploaded bundle visible to serving.
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SUCCEEDED",
            serving_status="AVAILABLE", serving_version=model_version,
            serving_as_of=as_of, run_id=run_id,
            diagnostics={**diagnostics, "embedding_count": count,
                         "evicted_embedding_count": evicted},
        )
        return count, evicted

    def reconcile_success():
        matched, stored = _reconcile_gnn_status(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            expected_status="SUCCEEDED",
            expected_version=model_version,
        )
        return matched, (
            int(stored.get("embedding_count", len(user_ids))),
            int(stored.get("evicted_embedding_count", 0)),
        )

    count, evicted = _run_publication_with_retry(
        conn,
        tenant_id=tenant_id,
        operation_name="publish-and-activate",
        operation=publish_success,
        reconcile=reconcile_success,
    )
    return f"OK ({model_version}, {selected}, {count} embeddings; {time.monotonic() - started:.1f}s)"


def _guard_lease(lease_guard: Callable[[], None] | None) -> None:
    if lease_guard is not None:
        lease_guard()


def _normalise_data_as_of_utc(value: datetime | None) -> datetime:
    """Return one timezone-aware UTC cutoff for all reads in a tenant run."""
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fence_lease(
    conn,
    lease_fence: Callable[[object], None] | None,
) -> None:
    if lease_fence is not None:
        lease_fence(conn)


def _process_tenant(
    conn,
    tenant_id: int,
    run_id: str | None = None,
    lease_guard: Callable[[], None] | None = None,
    lease_fence: Callable[[object], None] | None = None,
    data_as_of_utc: datetime | None = None,
) -> str:
    t0 = time.monotonic()
    run_id = run_id or str(uuid.uuid4())
    data_as_of_utc = _normalise_data_as_of_utc(data_as_of_utc)
    source = connect_award()
    try:
        configured_window_days = get_tenant_gnn_window(source, tenant_id)
        policy = load_active_policy(conn, tenant_id, configured_window_days)
        dataset = (
            load_award_nomination_dataset(
                SourceReadRequest(
                    tenant_id=tenant_id,
                    as_of_exclusive=data_as_of_utc,
                    window_days=policy.window_days,
                ),
                source_connection=source,
                sentinel_connection=conn,
            )
            if policy is not None
            else None
        )
    finally:
        source.close()
    if policy is None:
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="NO_ACTIVE_POLICY",
            reason_detail="No active integrity.GNNScoringPolicies row exists for this tenant.",
            diagnostics={"gnn_policy_available": False},
            run_id=run_id,
        )
        return "SKIPPED (no active GNN scoring policy)"
    if not policy.training_enabled:
        incumbent_selection = _incumbent_selection(conn, tenant_id)
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
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
    incumbent_specialists = _incumbent_specialists(conn, tenant_id)
    incumbent = (
        incumbent_selection.get("selected_architecture")
        if incumbent_selection else None
    )

    if dataset is None:
        raise RuntimeError("Canonical dataset was not loaded for the active GNN policy")
    users = actor_rows(dataset)
    nominations = gnn_nomination_rows(dataset)
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
        "data_as_of_utc": data_as_of_utc.isoformat(),
        **({"selection": incumbent_selection} if incumbent_selection else {}),
    }
    if (len(behavior_nominations) < policy.minimum_training_samples
            or len(users) < policy.minimum_users):
        detail = (f"{len(behavior_nominations)} nominations / {len(users)} users; "
                  f"requires {policy.minimum_training_samples} / {policy.minimum_users}")
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
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

    label_df = label_frame(dataset)
    labels_mod.summarise(label_df, tenant_id)

    # True training independence: only model-neutral outcomes may enter the GNN
    # loss. These are human adjudications plus isolated synthetic ground truth
    # for a tenant explicitly marked synthetic. Random Forest scores and
    # unexamined rows remain graph edges, but neither is a target.
    labelled = labels_mod.supervised_targets(label_df)
    label_source_counts = {
        str(source): int(count)
        for source, count in labelled["LabelSource"].value_counts().items()
    }
    label_map = dict(zip(labelled["NominationId"], labelled["IsFraud"]))
    if not label_map:
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
        upsert_component_status(
            conn, tenant_id=tenant_id, component="GNN", attempt_status="SKIPPED",
            reason_code="NO_ELIGIBLE_SUPERVISED_LABELS",
            reason_detail=(
                "No eligible human outcomes or isolated synthetic ground-truth "
                "labels are available for GNN training."
            ),
            diagnostics={
                **base_diagnostics,
                "supervised_label_count": 0,
                "label_source_counts": label_source_counts,
            },
            run_id=run_id,
        )
        return "SKIPPED (no eligible supervised nominations)"

    try:
        G.rolling_thresholds(
            behavior_nominations,
            n_folds=policy.rolling_folds,
        )
    except ValueError as exc:
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
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
        causal_window_days=policy.window_days,
    )

    y_train_by_fold = [
        _retain_labelled_targets(fold, "train", label_map) for fold in folds
    ]
    y_eval_by_fold = [
        _retain_labelled_targets(fold, "eval", label_map) for fold in folds
    ]
    y_ev = y_eval_by_fold[-1]
    y_tr = np.concatenate(y_train_by_fold)
    holdout_graph = folds[-1]

    train_pos = int(y_tr.sum())
    eval_pos = int(y_ev.sum())
    train_neg = int(len(y_tr) - train_pos)
    eval_neg = int(len(y_ev) - eval_pos)
    if (policy.serving_mode != SERVING_MODE_SPECIALISTS
            and (train_pos < policy.minimum_positives_per_split
                 or eval_pos < policy.minimum_positives_per_split)):
        detail = (
            f"rolling-train fraud labels {train_pos}, final-holdout fraud labels "
            f"{eval_pos}; requires {policy.minimum_positives_per_split} in each population"
        )
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
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
        return (f"SKIPPED (too few supervised fraud labels: train {train_pos}, "
                f"eval {eval_pos}, need {policy.minimum_positives_per_split} each)")
    if (policy.serving_mode != SERVING_MODE_SPECIALISTS
            and (train_neg == 0 or eval_neg == 0)):
        detail = (f"train {train_pos} fraud/{train_neg} legitimate; "
                  f"eval {eval_pos} fraud/{eval_neg} legitimate")
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
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
        return (f"SKIPPED (supervised labels need both classes: "
                f"train {train_pos} fraud/{train_neg} legitimate, "
                f"eval {eval_pos} fraud/{eval_neg} legitimate)")

    # All policy, incumbent, graph-source, and label reads are now complete.
    # Do not carry an idle Azure SQL session through the CPU-heavy evaluation
    # and blob-upload phases; publication below will lazily open a fresh one.
    conn.release()
    logger.info(
        "Tenant %d: SQL read phase complete; connection closed before model compute",
        tenant_id,
    )

    if policy.serving_mode == SERVING_MODE_SHARED_MULTI_HEAD:
        return _process_shared_multi_head(
            conn, tenant_id=tenant_id, run_id=run_id, policy=policy,
            users=users, nominations=nominations, labelled=labelled,
            folds=folds, base_diagnostics=base_diagnostics,
            label_source_counts=label_source_counts, started=t0,
            data_as_of_utc=data_as_of_utc,
            lease_guard=lease_guard,
            lease_fence=lease_fence,
        )

    candidate_models: dict[str, object] = {}
    candidate_metrics: dict[str, dict] = {}
    selection: dict = {
        "serving_mode": policy.serving_mode,
        "selected_architecture": None,
        "selection_reason": "SPECIALIST_SELECTION_BY_TRACK",
        "candidates": {},
    }
    if policy.serving_mode != SERVING_MODE_SPECIALISTS:
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

    scenario_by_nomination_id = {
        int(row.NominationId): str(row.ScenarioFamily).upper()
        for row in labelled.itertuples()
        if isinstance(getattr(row, "ScenarioFamily", None), str)
        and getattr(row, "ScenarioFamily").strip()
    }
    try:
        graph_value_evaluation = evaluate_graph_value(
            folds=folds,
            y_train_by_fold=y_train_by_fold,
            y_eval_by_fold=y_eval_by_fold,
            graph_architectures=policy.candidate_architectures,
            scenario_by_nomination_id=scenario_by_nomination_id,
            hidden_dim=policy.hidden_dim,
            emb_dim=policy.embed_dim,
            epochs=policy.epochs,
        )
    except Exception as exc:
        # Diagnostics must remain visible when they fail, but they are not a
        # serving gate and therefore cannot change the admission decision.
        logger.exception("GNN graph-value evaluation failed")
        graph_value_evaluation = {
            "schema_version": 1,
            "evaluator": "graph-value-by-ablation-v1",
            "role": "DIAGNOSTIC_ONLY",
            "affects_serving_selection": False,
            "status": "FAILED",
            "reason": type(exc).__name__,
            "detail": str(exc)[:500],
        }

    specialist_evaluation = None
    specialist_label_maps: dict[str, dict[int, int]] = {}
    if policy.serving_mode == SERVING_MODE_SPECIALISTS:
        specialist_label_maps = build_specialist_label_maps(labelled)
        specialist_evaluation = evaluate_specialists(
            tracks=policy.specialist_tracks,
            folds=folds,
            label_maps=specialist_label_maps,
            hidden_dim=policy.hidden_dim,
            emb_dim=policy.embed_dim,
            epochs=policy.epochs,
            incumbent_specialists=incumbent_specialists,
        )
        specialist_evaluation["serving_mode"] = policy.serving_mode

    # Candidate evaluation remains tied to the untouched holdout. Only after
    # selection is final do we admit that matured interval to a fresh refit.
    graph = G.build_serving_graph(
        users,
        nominations,
        causal_window_days=policy.window_days,
    )
    as_of = data_as_of_utc.date()
    run_suffix = run_id.replace("-", "")[:8]
    generation = "v3" if policy.serving_mode == SERVING_MODE_SPECIALISTS else "v2"
    model_version = f"gnn-{generation}-{as_of:%Y%m%d}-t{tenant_id}-{run_suffix}"
    graph_snapshot_id = (
        f"gnn-graph-{generation}-{as_of:%Y%m%d}-t{tenant_id}-{run_suffix}"
    )
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
    if specialist_evaluation is not None:
        specialist_root = bundle_dir / "specialists"
        specialist_root.mkdir(parents=True, exist_ok=True)
        for track, evaluation in specialist_evaluation["tracks"].items():
            track_dir = specialist_root / track.lower()
            track_dir.mkdir(parents=True, exist_ok=True)
            evaluation_path = track_dir / "evaluation.json"
            write_manifest(evaluation_path, evaluation)
            artifact_paths.append(
                (evaluation_path, f"specialist_{track.lower()}_evaluation")
            )
    specialist_models: dict[str, tuple[object, str]] = {}
    specialist_serving: dict | None = None
    if specialist_evaluation is not None:
        specialist_models, specialist_serving, specialist_artifacts = (
            _fit_admitted_specialists(
                policy=policy,
                folds=folds,
                label_maps=specialist_label_maps,
                evaluation=specialist_evaluation,
                serving_graph=graph,
                tenant_id=tenant_id,
                bundle_version=model_version,
                graph_snapshot_id=graph_snapshot_id,
                run_suffix=run_suffix,
                bundle_dir=bundle_dir,
                incumbents=incumbent_specialists,
            )
        )
        artifact_paths.extend(specialist_artifacts)
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
        graph_value_evaluation=graph_value_evaluation,
        specialist_evaluation=specialist_evaluation,
        specialist_serving=specialist_serving,
        artifact_paths=artifact_paths,
        manifest_path=manifest_path,
        policy=policy,
    )
    artifact_paths.append((manifest_path, "operational_manifest"))

    # Upload the whole immutable run before the SQL serving pointer changes.
    versioned_folder = gnn_bundle_prefix(tenant_id, model_version)
    bundle_uploads = []
    for artifact, _role in artifact_paths:
        relative_parent = artifact.relative_to(bundle_dir).parent.as_posix()
        blob_folder = (
            versioned_folder
            if relative_parent == "."
            else f"{versioned_folder}/{relative_parent}"
        )
        bundle_uploads.append(
            upload_artifact(artifact, blob_folder=blob_folder)
        )
    if os.getenv("AZURE_STORAGE_ACCOUNT") and not all(bundle_uploads):
        raise RuntimeError(
            "GNN candidate bundle upload was incomplete; serving pointer was preserved"
        )

    common_diagnostics = {
        **base_diagnostics,
        "supervised_eval_count": len(y_ev),
        "supervised_train_count": len(y_tr),
        "label_source_counts": label_source_counts,
        "rolling_fold_count": len(folds),
        "holdout_start": holdout_graph["t_cut"].isoformat(),
        "holdout_end": holdout_graph["eval_end"].isoformat(),
        "selection": selection,
        "graph_value_evaluation": graph_value_evaluation,
        "specialist_evaluation": specialist_evaluation,
        "specialists": specialist_serving,
        "feature_schema_version": graph["feature_schema_version"],
        "graph_snapshot_id": graph_snapshot_id,
        "graph_snapshot_as_of": graph.get(
            "graph_snapshot_as_of", graph["t_graph"]
        ).isoformat(),
        "artifact_bundle_prefix": versioned_folder,
    }
    active_specialist_count = sum(
        row.get("state") in {"ACTIVE", "CARRIED_FORWARD"}
        for row in (specialist_serving or {}).values()
    )
    if (
        policy.serving_mode == SERVING_MODE_SPECIALISTS
        and active_specialist_count == 0
    ):
        reason = "NO_ACTIVE_SPECIALIST_MODEL"

        def publish_specialist_skip():
            _guard_lease(lease_guard)
            _fence_lease(conn, lease_fence)
            upsert_component_status(
                conn,
                tenant_id=tenant_id,
                component="GNN",
                attempt_status="SKIPPED",
                reason_code=reason,
                reason_detail=(
                    "No behavior track admitted a graph model; the current serving "
                    "bundle was preserved."
                ),
                diagnostics=common_diagnostics,
                run_id=run_id,
            )

        def reconcile_specialist_skip():
            matched, _diagnostics = _reconcile_gnn_status(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                expected_status="SKIPPED",
            )
            return matched, None

        _run_publication_with_retry(
            conn,
            tenant_id=tenant_id,
            operation_name="activate-specialist-skip",
            operation=publish_specialist_skip,
            reconcile=reconcile_specialist_skip,
        )
        return (
            f"SKIPPED ({reason}; incumbent bundle preserved; "
            f"{time.monotonic() - t0:.1f}s)"
        )
    if policy.serving_mode != SERVING_MODE_SPECIALISTS and serving_model is None:
        reason = selection["selection_reason"]
        skipped_diagnostics = {
            **common_diagnostics,
            "selection": incumbent_selection or selection,
            "last_candidate_selection": selection,
        }

        def publish_candidate_skip():
            _guard_lease(lease_guard)
            _fence_lease(conn, lease_fence)
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

        def reconcile_candidate_skip():
            matched, _diagnostics = _reconcile_gnn_status(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                expected_status="SKIPPED",
            )
            return matched, None

        _run_publication_with_retry(
            conn,
            tenant_id=tenant_id,
            operation_name="activate-candidate-skip",
            operation=publish_candidate_skip,
            reconcile=reconcile_candidate_skip,
        )
        return (
            f"SKIPPED ({reason}; incumbent {incumbent or 'none'} preserved; "
            f"{time.monotonic() - t0:.1f}s)"
        )

    user_ids = sorted(
        graph["user_index"], key=lambda user_id: graph["user_index"][user_id]
    )
    embedding_batches = []
    if policy.serving_mode == SERVING_MODE_SPECIALISTS:
        for _key, (model, specialist_version) in specialist_models.items():
            with torch.no_grad():
                z = model.embed_users(graph["data"]).numpy().astype(np.float32)
            embedding_batches.append((specialist_version, z))
    else:
        with torch.no_grad():
            z = serving_model.embed_users(graph["data"]).numpy().astype(np.float32)
        embedding_batches.append((model_version, z))

    def publish_success():
        n_emb = 0
        for published_version, embeddings in embedding_batches:
            _guard_lease(lease_guard)
            _fence_lease(conn, lease_fence)
            n_emb += _publish_embeddings(
                conn,
                tenant_id,
                user_ids,
                embeddings,
                as_of,
                published_version,
            )
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
        n_evicted = _evict_stale_embeddings(
            conn, tenant_id, policy.embedding_retention_days
        )

        # This upsert is the activation pointer and therefore happens last.
        _guard_lease(lease_guard)
        _fence_lease(conn, lease_fence)
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
                "active_specialist_count": active_specialist_count,
                **(
                    {
                        "holdout_pr_auc": selection["selected_metric_value"],
                        "serving_refit_training_count": serving_metrics["n_train"],
                    }
                    if policy.serving_mode != SERVING_MODE_SPECIALISTS
                    else {}
                ),
            },
        )
        return n_emb, n_evicted

    def reconcile_success():
        matched, stored = _reconcile_gnn_status(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            expected_status="SUCCEEDED",
            expected_version=model_version,
        )
        return matched, (
            int(stored.get("embedding_count", len(user_ids) * len(embedding_batches))),
            int(stored.get("evicted_embedding_count", 0)),
        )

    n_emb, n_evicted = _run_publication_with_retry(
        conn,
        tenant_id=tenant_id,
        operation_name="publish-and-activate",
        operation=publish_success,
        reconcile=reconcile_success,
    )

    if policy.serving_mode == SERVING_MODE_SPECIALISTS:
        return (
            f"OK ({model_version}, {active_specialist_count} specialists, "
            f"{n_emb} embeddings, {n_evicted} evicted, "
            f"{time.monotonic() - t0:.1f}s)"
        )
    return (f"OK ({model_version}, {selected_architecture}, {n_emb} embeddings, "
            f"{n_evicted} evicted, supervised holdout PR-AUC "
            f"{selection['selected_metric_value']:.4f}, "
            f"{time.monotonic() - t0:.1f}s)")


# ── Entry point ───────────────────────────────────────────────────────────────

def process_tenant(
    tenant_id: int,
    run_id: str,
    lease_guard: Callable[[], None] | None = None,
    lease_fence: Callable[[object], None] | None = None,
    data_as_of_utc: datetime | None = None,
) -> TenantStageResult:
    """Train one tenant and translate component status into stage history."""
    conn = RenewableConnection(connect, log_context=f"Tenant {tenant_id} GNN")
    try:
        result_text = _process_tenant(
            conn,
            tenant_id,
            run_id,
            lease_guard,
            lease_fence,
            data_as_of_utc,
        )
        # Read the committed outcome through a separate short-lived session.
        conn.discard()
        row = _read_gnn_outcome_with_retry(conn, tenant_id)
        if row is None:
            raise RuntimeError("GNN stage completed without component status")
        attempt_status = str(row[0])
        diagnostics = {"result": result_text}
        if attempt_status == "SUCCEEDED":
            return TenantStageResult.succeeded(
                published_version=str(row[2]) if row[2] else None,
                diagnostics=diagnostics,
            )
        return TenantStageResult.skipped(
            str(row[1] or attempt_status),
            diagnostics=diagnostics,
        )
    except LeaseLostError:
        conn.discard()
        raise
    except Exception as exc:
        # Never try to reuse a connection that may have raised 08S01 or have an
        # unknown transaction state. Failure status gets its own fresh session.
        failure_detail = str(exc)
        conn.discard()
        _guard_lease(lease_guard)
        try:
            def publish_failure():
                _guard_lease(lease_guard)
                _fence_lease(conn, lease_fence)
                upsert_component_status(
                    conn,
                    tenant_id=tenant_id,
                    component="GNN",
                    attempt_status="FAILED",
                    reason_code="TRAINING_FAILED",
                    reason_detail=failure_detail,
                    run_id=run_id,
                )

            def reconcile_failure():
                matched, _diagnostics = _reconcile_gnn_status(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    expected_status="FAILED",
                )
                return matched, None

            _run_publication_with_retry(
                conn,
                tenant_id=tenant_id,
                operation_name="record-failure",
                operation=publish_failure,
                reconcile=reconcile_failure,
            )
        except Exception:
            logger.error(
                "Tenant %d GNN failure status could not be persisted",
                tenant_id,
                exc_info=True,
            )
        raise
    finally:
        conn.close()
        _log_peak_rss(f"after tenant {tenant_id}")


def main(tenants_to_process: list | None = None) -> None:
    """Called by run_job.py. Signature matches every other stage."""
    run_id = str(uuid.uuid4())
    data_as_of_utc = datetime.now(timezone.utc)
    logger.info("GNN MODEL TRAINING - Multi-Tenant")
    logger.info(
        "Each tenant's active integrity.GNNScoringPolicies row is read immediately "
        "before that tenant is processed."
    )

    conn = connect_award()
    try:
        tenants = _get_tenants(conn)
        if tenants_to_process is not None:
            tenants = [t for t in tenants if t in tenants_to_process]
            if not tenants:
                logger.warning("Tenant(s) %s not found. Exiting.", tenants_to_process)
                return
        logger.info("Tenants: %s", tenants)

    finally:
        conn.close()

    results, failed = {}, []
    for tenant_id in tenants:
        logger.info("Tenant %d", tenant_id)
        try:
            outcome = process_tenant(
                tenant_id, run_id, data_as_of_utc=data_as_of_utc
            )
            results[tenant_id] = outcome.status
        except Exception as exc:
            logger.error("Tenant %d failed: %s", tenant_id, exc, exc_info=True)
            results[tenant_id] = f"FAILED — {exc}"
            failed.append(tenant_id)

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
