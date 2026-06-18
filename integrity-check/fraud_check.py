"""
fraud_check.py — Async fraud assessment engine for the auxiliary service.
=========================================================================

Owns the full fraud detection pipeline:
  • Per-tenant RF model cache (blob-direct, lazy-loaded, process-lifetime)
  • Sentence-transformer embedding cache (module singleton)
  • Feature engineering — behavioural + semantic, mirrors train_fraud_model.py
  • RF inference — predict_proba → fraud_score / risk_level / warning_flags

Public API 
----------
    result = assess(nomination_details, tenant_id)

    result is a dict:
        fraud_score      int       0–100
        fraud_prob       float     0.0–1.0
        risk_level       str       NONE | LOW | MEDIUM | HIGH | CRITICAL
        warning_flags    list[str]
        flagged          bool      True when risk_level in MEDIUM/HIGH/CRITICAL
        model_available  bool      False when no pkl exists yet for the tenant

Called exclusively by handlers/nomination_submitted.py.
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
from datetime import datetime

import numpy as np

import db

logger = logging.getLogger("auxiliary.fraud_check")

# ── Config ────────────────────────────────────────────────────────────────────
_STORAGE_ACCOUNT = os.environ["AZURE_STORAGE_ACCOUNT"]
_MODEL_CONTAINER  = os.getenv("MODEL_CONTAINER", "ml-models")
_STORAGE_KEY      = os.getenv("AZURE_STORAGE_KEY")   # local dev only
_MI_CLIENT_ID     = os.getenv("MI_CLIENT_ID") or None


# ── Per-tenant model cache ────────────────────────────────────────────────────
# Streamed from blob on first assess() call per tenant, held for the process
# lifetime. KEDA scales the container to zero when the queue drains, so the
# cache naturally evicts between bursts — no TTL needed.

_model_cache: dict[int, dict | None] = {}
_model_cache_lock = threading.Lock()


def _get_model(tenant_id: int) -> dict | None:
    """Return cached model, streaming from blob on first access."""
    with _model_cache_lock:
        if tenant_id in _model_cache:
            return _model_cache[tenant_id]

    # Stream outside the lock so other tenants aren't blocked.
    model_data = _stream_from_blob(tenant_id)

    with _model_cache_lock:
        if tenant_id not in _model_cache:
            _model_cache[tenant_id] = model_data
            if model_data is not None:
                logger.info("Fraud model cached for tenant %d", tenant_id)

    return _model_cache[tenant_id]


def _stream_from_blob(tenant_id: int) -> dict | None:
    from azure.storage.blob import BlobServiceClient
    blob_name = f"fraud_detection_model_tenant_{tenant_id}.pkl"

    if _STORAGE_KEY:
        conn_str = (
            f"DefaultEndpointsProtocol=https;"
            f"AccountName={_STORAGE_ACCOUNT};"
            f"AccountKey={_STORAGE_KEY};"
            f"EndpointSuffix=core.windows.net"
        )
        client = BlobServiceClient.from_connection_string(conn_str)
    else:
        from azure.identity import DefaultAzureCredential
        client = BlobServiceClient(
            f"https://{_STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=DefaultAzureCredential(managed_identity_client_id=_MI_CLIENT_ID),
        )

    try:
        blob = client.get_blob_client(container=_MODEL_CONTAINER, blob=blob_name)
        data = blob.download_blob().readall()
        logger.info("Streamed fraud model from blob (%d bytes)", len(data),
                    extra={"tenant_id": tenant_id})
        return pickle.loads(data)
    except Exception as exc:
        from azure.core.exceptions import ResourceNotFoundError
        if isinstance(exc, ResourceNotFoundError):
            logger.warning("No fraud model blob for tenant %d — will route as clean", tenant_id)
        else:
            logger.error("Error streaming fraud model for tenant %d: %s", tenant_id, exc)
        return None


# ── Embedding model cache (keyed by model name) ───────────────────────────────
# Multiple tenants may use different sentence-transformer models (e.g.
# 'all-MiniLM-L6-v2' for English, 'paraphrase-multilingual-MiniLM-L12-v2'
# for Korean/Japanese/etc.).  Each model is loaded once and cached for the
# process lifetime.  description_check.py delegates here so both modules
# share the same loaded instances.

_embed_models:      dict[str, object] = {}
_embed_models_lock = threading.Lock()


def _get_embed_model(model_name: str = "all-MiniLM-L6-v2"):
    if model_name in _embed_models:
        return _embed_models[model_name]
    with _embed_models_lock:
        if model_name not in _embed_models:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformer '%s' …", model_name)
            _embed_models[model_name] = SentenceTransformer(model_name)
            logger.info("Sentence-transformer '%s' loaded.", model_name)
    return _embed_models[model_name]


# ── Feature engineering ───────────────────────────────────────────────────────

def _build_features(details: dict, model_data: dict) -> tuple[np.ndarray, float]:
    """
    Build and scale the feature vector from nomination details + DB lookups.
    Mirrors train_fraud_model.py extract_features() so training and inference
    stay aligned.

    Returns (X_scaled, desc_cosine_sim) — the cosine sim is also returned
    separately so warning flag generation doesn't need to re-index into X_scaled.
    """
    nominator_id    = details["nominator_id"]
    beneficiary_id  = details["beneficiary_id"]
    approver_id     = details["approver_id"]
    amount          = details["amount"]
    nomination_date = datetime.utcnow()

    # ── Nominator behaviour ───────────────────────────────────────────────────
    nom_hist = db.get_nominator_history(nominator_id)
    if nom_hist:
        nom_total       = len(nom_hist)
        nom_amounts     = [r[2] for r in nom_hist]
        nom_unique_bens = len(set(r[1] for r in nom_hist))
        nom_avg_amt     = float(np.mean(nom_amounts))
        nom_std_amt     = float(np.std(nom_amounts)) if nom_total > 1 else 0.0
    else:
        nom_total = nom_avg_amt = nom_std_amt = 0
        nom_unique_bens = 0

    # ── Beneficiary behaviour ─────────────────────────────────────────────────
    ben_hist = db.get_beneficiary_history(beneficiary_id)
    if ben_hist:
        ben_total   = len(ben_hist)
        ben_avg_amt = float(np.mean([r[2] for r in ben_hist]))
    else:
        ben_total = ben_avg_amt = 0

    # ── Approver behaviour ────────────────────────────────────────────────────
    appr_hist = db.get_approver_history(approver_id)
    if appr_hist:
        appr_total    = len(appr_hist)
        appr_times    = [r[1] for r in appr_hist if r[1] is not None]
        appr_avg_time = float(np.mean(appr_times)) if appr_times else 24.0
    else:
        appr_total    = 0
        appr_avg_time = 24.0

    # ── Relationship features ─────────────────────────────────────────────────
    has_reciprocal = db.check_reciprocal_nomination(nominator_id, beneficiary_id)
    pair_count     = db.get_pair_nomination_count(nominator_id, beneficiary_id)

    # ── Temporal features ─────────────────────────────────────────────────────
    day_of_week = nomination_date.weekday()
    month       = nomination_date.month
    is_weekend  = 1 if day_of_week in (5, 6) else 0

    # ── Amount z-score (tenant-scoped from training) ──────────────────────────
    amt_mean = model_data.get("amount_mean")
    amt_std  = model_data.get("amount_std")
    amount_zscore = (
        (amount - amt_mean) / amt_std
        if amt_mean is not None and amt_std and amt_std > 0
        else 0.0
    )
    is_high_amount      = 1 if amount_zscore > 2 else 0
    concentration_ratio = nom_total / (nom_unique_bens + 1)

    # ── Category target encoding ──────────────────────────────────────────────
    category_id        = details.get("category_id")
    cat_map            = model_data.get("category_fraud_rate", {})
    global_fraud_rate  = model_data.get("global_fraud_rate", 0.0)
    category_fraud_rate = (
        cat_map.get(category_id, global_fraud_rate)
        if category_id is not None else 0.0
    )

    # ── Semantic description features ─────────────────────────────────────────
    from sklearn.metrics.pairwise import cosine_similarity as sk_cosine_sim
    nom_description  = details.get("description") or ""
    embed_model_name = model_data.get("embed_model_name", "all-MiniLM-L6-v2")
    embed_model      = _get_embed_model(embed_model_name)
    ben_past_descs   = db.get_beneficiary_descriptions(beneficiary_id)

    if nom_description.strip() and ben_past_descs:
        nom_emb  = embed_model.encode([nom_description], normalize_embeddings=True)
        ben_embs = embed_model.encode(ben_past_descs,    normalize_embeddings=True)
        ben_mean = ben_embs.mean(axis=0, keepdims=True)
        desc_cosine_sim   = float(sk_cosine_sim(nom_emb, ben_mean)[0][0])
        desc_emb_distance = float(np.linalg.norm(nom_emb[0] - ben_mean[0]))
    else:
        desc_cosine_sim   = 0.0
        desc_emb_distance = 1.0

    # ── Assemble + scale ──────────────────────────────────────────────────────
    feature_cols = model_data["p2p_feature_columns"]
    feature_vals = {
        "Amount":                       amount,
        "DayOfWeek":                    day_of_week,
        "Month":                        month,
        "IsWeekend":                    is_weekend,
        "NominatorTotalNominations":    nom_total,
        "NominatorAvgAmount":           nom_avg_amt,
        "NominatorStdAmount":           nom_std_amt,
        "NominatorUniqueBeneficiaries": nom_unique_bens,
        "BeneficiaryTotalReceived":     ben_total,
        "BeneficiaryAvgAmountReceived": ben_avg_amt,
        "HasReciprocalNomination":      1 if has_reciprocal else 0,
        "PairNominationCount":          pair_count,
        "AmountZScore":                 amount_zscore,
        "IsHighAmount":                 is_high_amount,
        "NominatorConcentrationRatio":  concentration_ratio,
        "CategoryFraudRate":            category_fraud_rate,
        "DescriptionCosineSim":         desc_cosine_sim,
        "DescriptionEmbDistance":       desc_emb_distance,
    }

    X = np.array([[feature_vals.get(c, 0.0) for c in feature_cols]], dtype=float)

    logger.info(
        "Fraud feature vector",
        extra={
            "nomination_id":  details.get("nomination_id"),
            "nominator_id":   nominator_id,
            "beneficiary_id": beneficiary_id,
            "amount":         amount,
            "amount_zscore":  round(amount_zscore, 3),
            "pair_count":     pair_count,
            "reciprocal":     int(has_reciprocal),
            "concentration":  round(float(concentration_ratio), 3),
            "cosine_sim":     round(float(desc_cosine_sim), 4),
            "emb_distance":   round(float(desc_emb_distance), 4),
        },
    )

    return model_data["p2p_scaler"].transform(X), desc_cosine_sim


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _risk_level(score: int) -> str:
    if score >= 80: return "CRITICAL"
    if score >= 60: return "HIGH"
    if score >= 40: return "MEDIUM"
    if score >= 20: return "LOW"
    return "NONE"


def _warning_flags(model_data: dict, X_scaled: np.ndarray,
                   desc_cosine_sim: float) -> list[str]:
    feat  = dict(zip(model_data["p2p_feature_columns"], X_scaled[0]))
    flags = []
    if feat.get("NominatorTotalNominations", 0) > 50:
        flags.append("High frequency nominator")
    if feat.get("PairNominationCount", 0) > 5:
        flags.append("Repeated beneficiary")
    if feat.get("HasReciprocalNomination", 0) == 1:
        flags.append("Reciprocal nomination detected")
    if feat.get("IsHighAmount", 0) == 1:
        flags.append("Unusually high amount")
    if feat.get("NominatorConcentrationRatio", 0) > 5:
        flags.append("Limited beneficiary diversity")
    if desc_cosine_sim > 0.85:
        flags.append("Nomination descriptions suspiciously similar")
    return flags


# ── Public API ────────────────────────────────────────────────────────────────

def assess(details: dict, tenant_id: int) -> dict:
    """
    Run the full fraud assessment for a nomination.

    Args:
        details:   nomination dict from db.get_nomination_details()
        tenant_id: used to select the correct per-tenant RF model

    Returns:
        {
            model_available: bool,
            fraud_score:     int,
            fraud_prob:      float,
            risk_level:      str,
            warning_flags:   list[str],
            flagged:         bool,
        }

    Never raises — on model load failure, returns model_available=False and
    the caller routes the nomination as clean.
    """
    model_data = _get_model(tenant_id)

    if model_data is None:
        return {
            "model_available": False,
            "fraud_score":     0,
            "fraud_prob":      0.0,
            "risk_level":      "NONE",
            "warning_flags":   [],
            "flagged":         False,
        }

    X_scaled, desc_cosine_sim = _build_features(details, model_data)

    rf    = model_data["p2p_model"]
    proba = rf.predict_proba(X_scaled)
    fraud_prob  = float(proba[0][1]) if proba.shape[1] >= 2 else 0.0
    fraud_score = int(fraud_prob * 100)
    risk        = _risk_level(fraud_score)
    flags       = _warning_flags(model_data, X_scaled, desc_cosine_sim)

    return {
        "model_available": True,
        "fraud_score":     fraud_score,
        "fraud_prob":      round(fraud_prob, 4),
        "risk_level":      risk,
        "warning_flags":   flags,
        "flagged":         risk in ("MEDIUM", "HIGH", "CRITICAL"),
    }
