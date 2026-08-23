"""
gnn_check.py — GNN fraud assessment for the integrity-check worker
===================================================================

Structural twin of fraud_check.py, for the third fraud model.

What runs here is only the DECODER. The weekly fraud-analytics-job trains a
heterogeneous GraphSAGE encoder, publishes per-user node embeddings to
dbo.GNN_UserEmbeddings, and uploads the decoder as gnn_head_tenant_<N>.pt.
Inference is three keyed embedding lookups plus a ~15k-parameter MLP forward
pass — no graph traversal, no PyTorch Geometric, no new dependency in this
image (torch is already here via sentence-transformers).

Public API
----------
    result = assess_gnn(nomination_details, tenant_id)

Returns the same shape as fraud_check.assess() so handler.py can treat both
models uniformly, plus two provenance fields:

    model_available    bool
    fraud_score        int         0-100
    fraud_prob         float       0.0-1.0
    risk_level         str         NONE | LOW | MEDIUM | HIGH | CRITICAL
    warning_flags      list[str]
    flagged            bool
    model_version      str | None
    embedding_as_of    date | None

Never raises. Every failure path returns model_available=False, which the
caller treats as "no GNN opinion" — not as "clean".

Rollback semantics — why the lookup is version-matched
-------------------------------------------------------
The model spans two artifacts that must agree: the decoder in blob storage and
the embeddings in SQL. An earlier design read the LATEST embedding snapshot per
user and then asserted its ModelVersion matched the decoder. That fails safe but
not useful: rolling the decoder back to last week's build makes every lookup
mismatch, and the model goes dark.

So the lookup selects the newest snapshot WHOSE ModelVersion MATCHES THE
DECODER. Because dbo.GNN_UserEmbeddings is append-only within its retention
window, last week's embeddings are still present, and restoring the previous
gnn_head_tenant_<N>.pt is sufficient to roll the whole model back — no SQL
surgery, no coordinated deploy.

The equality assert is kept anyway, as a cheap invariant check on a path where
being wrong is worse than being unavailable.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone

import numpy as np
import torch

from utils import db

logger = logging.getLogger("integrity_check.gnn_check")

# ── Config ────────────────────────────────────────────────────────────────────
_STORAGE_ACCOUNT = os.environ["AZURE_STORAGE_ACCOUNT"]
_MODEL_CONTAINER = os.getenv("MODEL_CONTAINER", "ml-models")
_STORAGE_KEY     = os.getenv("AZURE_STORAGE_KEY")   # local dev only

# Beyond this the embeddings still score, but the result is flagged and the
# staleness is recorded on the row. The weekly cadence means ~7 days is normal;
# 14 means a run was missed.
_STALE_EMBEDDING_DAYS = int(os.getenv("GNN_STALE_EMBEDDING_DAYS", "14"))

# Sentinel matching gnn_model._NO_APPROVER — an absent approver contributes a
# zero vector rather than suppressing the score.
_ZERO_APPROVER = True


# ── Per-tenant decoder cache ──────────────────────────────────────────────────
# Streamed from blob on first assess_gnn() call per tenant and held for the
# process lifetime. KEDA scales this container to zero when the queue drains, so
# the cache evicts naturally between bursts — same reasoning as fraud_check.py.

_head_cache: dict[int, dict | None] = {}
_head_cache_lock = threading.Lock()


def _get_head(tenant_id: int) -> dict | None:
    with _head_cache_lock:
        if tenant_id in _head_cache:
            return _head_cache[tenant_id]

    # Stream outside the lock so other tenants are not blocked.
    head = _stream_head_from_blob(tenant_id)

    with _head_cache_lock:
        if tenant_id not in _head_cache:
            _head_cache[tenant_id] = head
            if head is not None:
                logger.info(
                    "GNN decoder cached for tenant %d (version=%s, emb_dim=%d)",
                    tenant_id, head.get("model_version"), head.get("emb_dim", -1),
                )
    return _head_cache[tenant_id]


def _stream_head_from_blob(tenant_id: int) -> dict | None:
    """
    Download and deserialise gnn_head_tenant_<N>.pt.

    weights_only=True is deliberate and load-bearing. torch.save uses pickle
    underneath, so a .pt file is as executable as a .pkl unless restricted. The
    artifact is written as a plain state_dict plus primitive metadata precisely
    so this restriction can be applied — it cannot execute code even if blob
    storage were compromised. Do not relax it to make a richer artifact load;
    change the artifact instead.

    (The default only became True in torch 2.6 and requirements.txt pins
    torch>=2.2.0, so it is passed explicitly rather than assumed.)
    """
    from azure.storage.blob import BlobServiceClient

    blob_name = f"gnn_head_tenant_{tenant_id}.pt"

    if _STORAGE_KEY:
        conn_str = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={_STORAGE_ACCOUNT};"
            f"AccountKey={_STORAGE_KEY};"
            f"EndpointSuffix=core.windows.net"
        )
        client = BlobServiceClient.from_connection_string(conn_str)
    else:
        from utils.azure_credential import credential
        client = BlobServiceClient(
            f"https://{_STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=credential,
        )

    try:
        blob = client.get_blob_client(container=_MODEL_CONTAINER, blob=blob_name)
        raw = blob.download_blob().readall()
    except Exception as exc:
        from azure.core.exceptions import ResourceNotFoundError
        if isinstance(exc, ResourceNotFoundError):
            logger.info(
                "No GNN decoder blob for tenant %d — tenant has no GNN model yet.",
                tenant_id,
            )
        else:
            logger.error("Error streaming GNN decoder for tenant %d: %s", tenant_id, exc)
        return None

    try:
        import io
        head = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    except Exception as exc:
        logger.error(
            "GNN decoder for tenant %d failed to deserialise (%d bytes): %s",
            tenant_id, len(raw), exc, exc_info=True,
        )
        return None

    # NOTE: user_scaler_* is intentionally NOT required. It is persisted in the
    # artifact for reproducibility, but must never be applied here — see
    # _assess_gnn_inner() for why.
    missing = {
        "decoder_state_dict", "model_version", "emb_dim",
        "nomination_scaler_mean", "nomination_scaler_std",
        "nomination_feature_columns", "amount_mean", "amount_std",
    } - set(head)
    if missing:
        logger.error(
            "GNN decoder for tenant %d is missing required keys: %s — refusing to score.",
            tenant_id, sorted(missing),
        )
        return None

    head["_module"] = _build_decoder(head)
    return head


def _build_decoder(head: dict):
    """
    Reconstruct the decoder from its state_dict.

    Defined inline rather than imported from fraud-analytics-job/gnn_model.py:
    that module imports torch_geometric at module scope, which is not installed
    in this image and must not be. The architecture is duplicated deliberately —
    the shape is asserted against the state_dict below, so a divergence fails
    loudly at load time rather than silently mis-scoring.
    """
    import torch.nn as nn

    emb_dim = int(head["emb_dim"])
    n_nom = len(head["nomination_feature_columns"])
    h1, h2 = head.get("decoder_hidden", (64, 32))

    module = nn.Sequential(
        nn.Linear(3 * emb_dim + n_nom, h1), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(h1, h2),                  nn.ReLU(),
        nn.Linear(h2, 1),
    )
    # Strict load: any architecture drift between trainer and this copy raises.
    module.load_state_dict(head["decoder_state_dict"], strict=True)
    module.eval()
    return module


# ── Feature preparation ───────────────────────────────────────────────────────

def _standardise(x: np.ndarray, mean, std) -> np.ndarray:
    """
    Apply the scaler persisted by the training run.

    Not optional. Raw features mix counts (~20) with currency amounts (~500);
    the decoder was fitted in standardised space and scoring in raw space
    produces confident nonsense with no error raised. Measured during
    development: PR-AUC 0.891 standardised versus 0.078 unstandardised.
    """
    mean = np.asarray(mean, dtype=np.float32)
    std = np.asarray(std, dtype=np.float32)
    std = np.where(std < 1e-8, 1.0, std)
    return ((x - mean) / std).astype(np.float32)


def _nomination_features(details: dict, head: dict) -> np.ndarray:
    """
    Build the nomination feature row, in the exact column order the trainer used.

    Column order comes from the artifact rather than a constant in this file, so
    a trainer-side change cannot silently permute the vector.
    """
    amount = float(details.get("amount") or 0.0)
    when = details.get("nomination_date") or datetime.now(timezone.utc)
    if isinstance(when, datetime):
        when = when.date()

    a_mean = float(head.get("amount_mean", 0.0))
    a_std = float(head.get("amount_std", 0.0))
    z = (amount - a_mean) / a_std if a_std > 0 else 0.0

    values = {
        "Amount":        amount,
        "AmountZScore":  z,
        "DayOfWeek":     float(when.weekday()),
        "Month":         float(when.month),
        "IsWeekend":     1.0 if when.weekday() >= 5 else 0.0,
        "IsHighAmount":  1.0 if amount > a_mean + 2.0 * a_std else 0.0,
        "HasApprover":   1.0 if details.get("approver_id") is not None else 0.0,
    }

    cols = head["nomination_feature_columns"]
    row = np.array([[values.get(c, 0.0) for c in cols]], dtype=np.float32)
    return _standardise(
        row, head["nomination_scaler_mean"], head["nomination_scaler_std"]
    )


# ── Risk mapping ──────────────────────────────────────────────────────────────

def _thresholds(tenant_id: int) -> dict:
    """
    GNN-specific routing thresholds from integrity_config.gnn.score_routing.

    Separate from the Random Forest's thresholds by design: the two models have
    different score distributions, and reusing one set would silently mis-tune
    whichever model was not calibrated for it.
    """
    cfg = db.get_tenant_integrity_config(tenant_id) or {}
    gnn = cfg.get("gnn", {}) if isinstance(cfg, dict) else {}
    gnn = gnn if isinstance(gnn, dict) else {}
    routing = gnn.get("score_routing", {})
    routing = routing if isinstance(routing, dict) else {}
    return {
        "critical": int(routing.get("critical_threshold", 85)),
        "high":     int(routing.get("high_threshold",     65)),
        "medium":   int(routing.get("medium_threshold",   45)),
        "low":      int(routing.get("low_threshold",      25)),
    }


def _risk_level(score: int, t: dict) -> str:
    if score >= t["critical"]:
        return "CRITICAL"
    if score >= t["high"]:
        return "HIGH"
    if score >= t["medium"]:
        return "MEDIUM"
    if score >= t["low"]:
        return "LOW"
    return "NONE"


def _unavailable(reason: str, flags: list[str] | None = None) -> dict:
    return {
        "model_available":  False,
        "unavailable_reason": reason,
        "fraud_score":      0,
        "fraud_prob":       0.0,
        "risk_level":       "NONE",
        "warning_flags":    flags or [],
        "flagged":          False,
        "model_version":    None,
        "embedding_as_of":  None,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def assess_gnn(details: dict, tenant_id: int) -> dict:
    """
    Score one nomination with the GNN decoder.

    Never raises. Returns model_available=False on any missing artifact, missing
    embedding, or unexpected error — the caller must treat that as "no opinion",
    never as "clean".
    """
    try:
        return _assess_gnn_inner(details, tenant_id)
    except Exception as exc:
        logger.error(
            "GNN assessment failed for nomination %s (tenant %d): %s",
            details.get("nomination_id"), tenant_id, exc, exc_info=True,
        )
        return _unavailable("exception")


def _assess_gnn_inner(details: dict, tenant_id: int) -> dict:
    head = _get_head(tenant_id)
    if head is None:
        return _unavailable("no_model")

    model_version = head["model_version"]
    nominator_id   = details["nominator_id"]
    beneficiary_id = details["beneficiary_id"]
    approver_id    = details.get("approver_id")

    wanted = [nominator_id, beneficiary_id]
    if approver_id is not None:
        wanted.append(approver_id)

    # Version-matched lookup — see the module docstring. Selecting the newest
    # snapshot for THIS decoder version is what makes a decoder-only rollback
    # work; "newest overall" would leave a rolled-back decoder permanently dark.
    embeddings = db.get_gnn_user_embeddings(
        tenant_id=tenant_id, user_ids=wanted, model_version=model_version
    )

    flags: list[str] = []

    # Nominator and beneficiary carry the relationship being scored. Without
    # either, there is nothing to score — suppress rather than substitute.
    if nominator_id not in embeddings or beneficiary_id not in embeddings:
        who = "nominator" if nominator_id not in embeddings else "beneficiary"

        # Distinguish two very different causes that look identical here.
        # A new joiner has no embeddings at all and is expected. A user who HAS
        # embeddings, but none for this decoder version, means the artifact pair
        # has come apart — a rollback that outran the retention window, or a
        # weekly run that uploaded a decoder without publishing embeddings.
        # The first is routine; the second needs someone to look.
        # Query ONLY the users actually missing. Asking about both would find the
        # present one's embeddings and misreport a genuine cold start as a
        # version gap.
        absent = [u for u in (nominator_id, beneficiary_id) if u not in embeddings]
        any_version = db.get_gnn_user_embeddings(tenant_id=tenant_id, user_ids=absent)
        if any_version:
            have = {u: v[2] for u, v in any_version.items()}
            logger.error(
                "GNN version gap: nomination %s tenant %d — decoder is %s but the "
                "only embeddings present are %s. Scoring suppressed; the decoder "
                "and dbo.GNN_UserEmbeddings have diverged.",
                details.get("nomination_id"), tenant_id, model_version, have,
            )
            return _unavailable("version_unavailable", ["[GNN] embedding version gap"])

        logger.info(
            "GNN cold-start: nomination %s tenant %d has no embeddings for %s",
            details.get("nomination_id"), tenant_id, who,
        )
        return _unavailable("cold_start_user", ["[GNN] cold-start user"])

    emb_dim = int(head["emb_dim"])

    def _vec(uid: int) -> np.ndarray:
        raw, as_of, version = embeddings[uid]
        if version != model_version:
            # Belt and braces: the query already filters on version.
            raise ValueError(
                f"embedding for user {uid} has version {version!r}, "
                f"decoder is {model_version!r}"
            )
        if raw.shape[0] != emb_dim:
            raise ValueError(
                f"embedding for user {uid} has dim {raw.shape[0]}, decoder expects {emb_dim}"
            )
        return raw

    z_nom = _vec(nominator_id)
    z_ben = _vec(beneficiary_id)

    # A missing approver is far less informative than a missing counterparty, so
    # it is tolerated with a zero vector and a flag — matching how the model was
    # trained (gnn_model._NO_APPROVER).
    if approver_id is not None and approver_id in embeddings:
        z_appr = _vec(approver_id)
    else:
        z_appr = np.zeros(emb_dim, dtype=np.float32)
        if approver_id is not None:
            flags.append("[GNN] approver cold-start")

    # Staleness is measured from the OLDEST contributing snapshot — the score is
    # only as fresh as its least fresh input.
    as_of_dates = [embeddings[u][1] for u in (nominator_id, beneficiary_id)]
    if approver_id is not None and approver_id in embeddings:
        as_of_dates.append(embeddings[approver_id][1])
    embedding_as_of = min(d for d in as_of_dates if d is not None)

    if embedding_as_of < date.today() - timedelta(days=_STALE_EMBEDDING_DAYS):
        flags.append("[GNN] stale embeddings")
        logger.warning(
            "GNN embeddings for tenant %d are %d days old (nomination %s)",
            tenant_id, (date.today() - embedding_as_of).days,
            details.get("nomination_id"),
        )

    # Scaling asymmetry — deliberate, and easy to get wrong.
    #
    # User embeddings are ENCODER OUTPUT. The user_scaler in the artifact was
    # applied to the raw user features on their way INTO the encoder, inside the
    # weekly job. By the time a vector reaches dbo.GNN_UserEmbeddings that
    # transform has already happened, and the decoder was trained on exactly
    # these values. Standardising them again here would place the decoder in a
    # feature space it has never seen.
    #
    # The nomination row is different: it bypasses the encoder entirely and is
    # fed to the decoder directly, so it needs the same standardisation the
    # trainer applied — hence _nomination_features() applies nomination_scaler.
    z = np.concatenate([
        z_nom.reshape(1, -1),
        z_ben.reshape(1, -1),
        z_appr.reshape(1, -1),
        _nomination_features(details, head),
    ], axis=1)

    with torch.no_grad():
        logit = head["_module"](torch.from_numpy(z)).squeeze()
        fraud_prob = float(torch.sigmoid(logit))

    fraud_score = int(round(fraud_prob * 100))
    thresholds = _thresholds(tenant_id)
    risk = _risk_level(fraud_score, thresholds)

    return {
        "model_available":  True,
        "fraud_score":      fraud_score,
        "fraud_prob":       round(fraud_prob, 4),
        "risk_level":       risk,
        "warning_flags":    flags,
        "flagged":          risk in ("MEDIUM", "HIGH", "CRITICAL"),
        "model_version":    model_version,
        "embedding_as_of":  embedding_as_of,
    }
