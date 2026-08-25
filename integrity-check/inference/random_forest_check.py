"""
random_forest_check.py — Random Forest nomination inference.
=========================================================================

Owns the full fraud detection pipeline:
  • Per-tenant RF model cache (blob-direct, lazy-loaded, process-lifetime)
  • Per-tenant SHAP TreeExplainer cache (lazy-created alongside model)
  • Sentence-transformer embedding cache (module singleton)
  • Feature engineering — behavioural + semantic, mirrors modeling/train_rf_model.py
  • RF inference — predict_proba → fraud_score / risk_level / warning_flags
  • SHAP attribution — top-5 feature contributions for flagged nominations
  • LLM explanation — human-readable rejection reason (CRITICAL auto-rejects)

Public API
----------
    result = assess(nomination_details, tenant_id)

    result is a dict:
        fraud_score         int         0–100
        fraud_prob          float       0.0–1.0
        risk_level          str         NONE | LOW | MEDIUM | HIGH | CRITICAL
        warning_flags       list[str]
        flagged             bool        True when risk_level in MEDIUM/HIGH/CRITICAL
        model_available     bool        False when no pkl exists yet for the tenant
        shap_explanations   list[dict]  Top-5 SHAP contributions (flagged only, else [])
        shap_status         str         COMPLETED | FAILED | SKIPPED
        shap_reason         str | None  Why SHAP was skipped or failed
        fraud_explanation   str | None  LLM-generated human text (CRITICAL only, else None)

Called exclusively by handler.py.
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
from datetime import datetime

import numpy as np

from . import component_availability
from utils import db

logger = logging.getLogger("integrity_check.random_forest")

# ── Config ────────────────────────────────────────────────────────────────────
_STORAGE_ACCOUNT = os.environ["AZURE_STORAGE_ACCOUNT"]
_MODEL_CONTAINER  = os.getenv("MODEL_CONTAINER", "ml-models")
_STORAGE_KEY      = os.getenv("AZURE_STORAGE_KEY")   # local dev only


# ── Per-tenant model cache ────────────────────────────────────────────────────
# Streamed from blob on first assess() call per tenant, held for the process
# lifetime. KEDA scales the container to zero when the queue drains, so the
# cache naturally evicts between bursts — no TTL needed.

_model_cache: dict[int, dict | None] = {}
_model_cache_lock = threading.Lock()

# ── Per-tenant integrity config cache ─────────────────────────────────────────
# Loaded from dbo.Tenants on first assess() call per tenant, held for the
# process lifetime. Config changes require a container restart — acceptable
# operational behaviour agreed with the team.

_integrity_config_cache: dict[int, dict] = {}
_integrity_config_cache_lock = threading.Lock()


def _get_integrity_config(tenant_id: int) -> dict:
    """Return cached integrity_config for the tenant, loading from DB on first access."""
    with _integrity_config_cache_lock:
        if tenant_id in _integrity_config_cache:
            return _integrity_config_cache[tenant_id]

    config = db.get_tenant_integrity_config(tenant_id)

    with _integrity_config_cache_lock:
        _integrity_config_cache[tenant_id] = config

    return config


def _score_routing_thresholds(tenant_id: int) -> dict:
    """
    Return score routing thresholds for the tenant.

    Reads from integrity_config.score_routing; falls back to system defaults
    for any missing key.  The returned dict is ready for _risk_level().
    """
    config      = _get_integrity_config(tenant_id)
    routing     = config.get("score_routing", {})
    return {
        "critical": int(routing.get("critical_threshold", 80)),
        "high":     int(routing.get("high_threshold",     60)),
        "medium":   int(routing.get("medium_threshold",   40)),
        "low":      int(routing.get("low_threshold",      20)),
    }


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
    blob_name = f"random_forest_tenant_{tenant_id}.pkl"

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

    from azure.core.exceptions import ResourceNotFoundError
    try:
        blob = client.get_blob_client(container=_MODEL_CONTAINER, blob=blob_name)
        data = blob.download_blob().readall()
        logger.info(
            "Streamed RF model %s from blob (%d bytes)", blob_name, len(data),
            extra={"tenant_id": tenant_id},
        )
        return pickle.loads(data)
    except ResourceNotFoundError:
        logger.warning(
            "No RF model blob %s for tenant %d; RF will contribute no opinion",
            blob_name, tenant_id,
        )
        return None
    except Exception as exc:
        logger.error("Error streaming RF model for tenant %d: %s", tenant_id, exc)
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

def _build_features(details: dict, model_data: dict) -> tuple[np.ndarray, dict, float]:
    """
    Build and scale the feature vector from nomination details + DB lookups.
    Mirrors modeling/train_rf_model.py extract_features() so training and inference
    stay aligned.

    Returns (X_scaled, feature_vals, desc_cosine_sim).
      X_scaled     — scaler-transformed array fed to the RF model
      feature_vals — raw unscaled dict (used for SHAP display values)
      desc_cosine_sim — returned separately so _warning_flags doesn't re-index
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
        _appr_total   = len(appr_hist)
        appr_times    = [r[1] for r in appr_hist if r[1] is not None]
        _appr_avg_time = float(np.mean(appr_times)) if appr_times else 24.0
    else:
        _appr_total    = 0
        _appr_avg_time = 24.0

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

    # ── Graph pattern features (UserGraphFlags latest snapshot) ──────────────
    tenant_id = details["tenant_id"]
    graph_flags = db.get_user_graph_flags(tenant_id, nominator_id, beneficiary_id)

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
        # Graph pattern features
        "GraphCycleFlag":            graph_flags["GraphCycleFlag"],
        "GraphReciprocalFlag":       1 if has_reciprocal else 0,
        "GraphClusterSize":          graph_flags["GraphClusterSize"],
        "SuperNominatorFlag":        graph_flags["SuperNominatorFlag"],
        "TransactionalLanguageFlag": graph_flags["TransactionalLanguageFlag"],
    }

    X = np.array([[feature_vals.get(c, 0.0) for c in feature_cols]], dtype=float)

    logger.info(
        "Fraud feature vector",
        extra={
            "nomination_id":          details.get("nomination_id"),
            "nominator_id":           nominator_id,
            "beneficiary_id":         beneficiary_id,
            "amount":                 amount,
            "amount_zscore":          round(amount_zscore, 3),
            "pair_count":             pair_count,
            "reciprocal":             int(has_reciprocal),
            "concentration":          round(float(concentration_ratio), 3),
            "cosine_sim":             round(float(desc_cosine_sim), 4),
            "emb_distance":           round(float(desc_emb_distance), 4),
            "graph_cycle":            graph_flags["GraphCycleFlag"],
            "graph_cluster_size":     graph_flags["GraphClusterSize"],
            "super_nominator":        graph_flags["SuperNominatorFlag"],
            "transactional_language": graph_flags["TransactionalLanguageFlag"],
        },
    )

    return model_data["p2p_scaler"].transform(X), feature_vals, desc_cosine_sim


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _risk_level(score: int, thresholds: dict) -> str:
    """
    Map a 0–100 fraud score to a risk level using per-tenant thresholds.

    thresholds must contain keys: critical, high, medium, low.
    Use _score_routing_thresholds(tenant_id) to build the dict.
    """
    if score >= thresholds["critical"]:
        return "CRITICAL"
    if score >= thresholds["high"]:
        return "HIGH"
    if score >= thresholds["medium"]:
        return "MEDIUM"
    if score >= thresholds["low"]:
        return "LOW"
    return "NONE"


def _warning_flags(
    model_data:    dict,
    X_scaled:      np.ndarray,
    desc_cosine_sim: float,
    feature_vals:  dict,
) -> list[str]:
    flags = []
    # Behavioural flags (thresholds applied to raw feature_vals for readability)
    if feature_vals.get("NominatorTotalNominations", 0) > 50:
        flags.append("High frequency nominator")
    if feature_vals.get("PairNominationCount", 0) > 5:
        flags.append("Repeated beneficiary")
    if feature_vals.get("HasReciprocalNomination", 0) == 1:
        flags.append("Reciprocal nomination detected")
    if feature_vals.get("IsHighAmount", 0) == 1:
        flags.append("Unusually high amount")
    if feature_vals.get("NominatorConcentrationRatio", 0) > 5:
        flags.append("Limited beneficiary diversity")
    if desc_cosine_sim > 0.85:
        flags.append("Nomination descriptions suspiciously similar")
    # Graph pattern flags
    if feature_vals.get("GraphCycleFlag", 0) == 1:
        flags.append("Nominator or beneficiary is part of a known nomination ring")
    if feature_vals.get("SuperNominatorFlag", 0) == 1:
        flags.append("Nominator is a statistical outlier in nomination volume")
    if feature_vals.get("GraphClusterSize", 0) > 0:
        flags.append(
            f"Nomination belongs to a copy-paste cluster "
            f"(size: {feature_vals['GraphClusterSize']})"
        )
    if feature_vals.get("TransactionalLanguageFlag", 0) == 1:
        flags.append("Transactional or quid-pro-quo language detected")
    return flags


# ── SHAP attribution ──────────────────────────────────────────────────────────

# Human-readable labels for each feature name used in prompts and UI.
_FEATURE_LABELS: dict[str, str] = {
    "PairNominationCount":          "number of times this nominator has nominated this beneficiary",
    "AmountZScore":                 "how far the award amount deviates from the tenant average",
    "NominatorConcentrationRatio":  "degree to which the nominator's awards are concentrated on few people",
    "HasReciprocalNomination":      "whether the beneficiary has also nominated the nominator",
    "NominatorTotalNominations":    "total nominations submitted by this nominator",
    "IsHighAmount":                 "whether the award amount is statistically high",
    "BeneficiaryTotalReceived":     "total awards this person has received",
    "BeneficiaryAvgAmountReceived": "average award amount this person typically receives",
    "DescriptionCosineSim":         "similarity of this description to past nominations for this beneficiary",
    "DescriptionEmbDistance":       "semantic distance of this description from prior nominations",
    "CategoryFraudRate":            "historical fraud rate for this award category",
    "NominatorAvgAmount":           "this nominator's typical award amount",
    "NominatorStdAmount":           "variability in this nominator's award amounts",
    "NominatorUniqueBeneficiaries": "number of distinct people this nominator has nominated",
    # Graph pattern features
    "GraphCycleFlag":            "whether this nominator or beneficiary is part of a known nomination ring",
    "GraphReciprocalFlag":       "whether the beneficiary has also nominated the nominator back",
    "GraphClusterSize":          "size of the copy-paste description cluster this nomination belongs to",
    "SuperNominatorFlag":        "whether the nominator is a statistical outlier in nomination volume",
    "TransactionalLanguageFlag": "whether transactional or quid-pro-quo language was detected in related nominations",
}


def _get_explainer(model_data: dict):
    """
    Lazily create a SHAP TreeExplainer for the tenant's RF model and cache it
    inside model_data so it is built at most once per process lifetime per tenant.
    """
    if "shap_explainer" not in model_data:
        import shap
        logger.info("Building SHAP TreeExplainer for RF model …")
        model_data["shap_explainer"] = shap.TreeExplainer(model_data["p2p_model"])
        logger.info("SHAP TreeExplainer ready.")
    return model_data["shap_explainer"]


def _compute_shap(
    model_data: dict,
    X_scaled: np.ndarray,
    feature_vals: dict,
    top_n: int = 5,
) -> list[dict]:
    """
    Run SHAP on the scaled feature vector and return the top_n features by
    absolute contribution to the fraud class probability.

    Returns a list of dicts ordered by |contribution| descending:
        [{"feature": str, "raw_value": float, "contribution": float}, ...]

    raw_value is the original unscaled value so the LLM and UI can show
    meaningful numbers ("nominated 7 times") rather than scaled floats.
    """
    explainer    = _get_explainer(model_data)
    feature_cols = model_data["p2p_feature_columns"]

    shap_vals = explainer.shap_values(X_scaled)
    # SHAP output shape varies by version:
    #   < 0.46  — list [class0_arr, class1_arr], each (n_samples, n_features)
    #   >= 0.46 — ndarray (n_samples, n_features, n_classes)
    # We want class-1 (fraud) contributions for sample 0 in all cases.
    if isinstance(shap_vals, list):
        fraud_shap = shap_vals[1][0]
    elif isinstance(shap_vals, np.ndarray) and shap_vals.ndim == 3:
        fraud_shap = shap_vals[0, :, 1]
    else:
        fraud_shap = shap_vals[0]

    contributions = [
        {
            "feature":      feature_cols[i],
            "raw_value":    round(float(feature_vals.get(feature_cols[i], 0.0)), 4),
            "contribution": round(float(fraud_shap[i]), 4),
        }
        for i in range(len(feature_cols))
    ]
    contributions.sort(key=lambda x: abs(x["contribution"]), reverse=True)
    return contributions[:top_n]


# ── LLM explanation ───────────────────────────────────────────────────────────

_FALLBACK_EXPLANATION = (
    "Your nomination was automatically declined because our fraud prevention "
    "system detected unusual patterns in this submission. If you believe this "
    "is an error, please contact your HR administrator for further information."
)


def _generate_explanation(shap_contributions: list[dict], fraud_score: int) -> str:
    """
    Call Azure OpenAI to convert SHAP feature contributions into a concise,
    non-accusatory rejection explanation suitable for the nominator.

    Falls back to _FALLBACK_EXPLANATION on any error so the caller is never
    blocked by an LLM failure.
    """
    import os
    from openai import AzureOpenAI

    try:
        client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        )

        # Build a readable summary of the top contributing signals.
        signal_lines = []
        for c in shap_contributions:
            label  = _FEATURE_LABELS.get(c["feature"], c["feature"])
            direction = "elevated" if c["contribution"] > 0 else "low"
            signal_lines.append(f"- {label}: {direction} (raw value: {c['raw_value']})")
        signals_text = "\n".join(signal_lines)

        prompt = (
            "You are an HR compliance system writing a brief, professional explanation "
            "for why an award nomination was automatically declined by a fraud detection model "
            f"(confidence score: {fraud_score}/100).\n\n"
            "The top signals that contributed to this decision are:\n"
            f"{signals_text}\n\n"
            "Write 2–3 sentences for the nominator. Rules:\n"
            "- Use plain English — no ML terminology, no feature names, no scores\n"
            "- Describe the pattern in neutral, factual language (e.g. 'multiple nominations "
            "to the same recipient' not 'you are committing fraud')\n"
            "- Do not be accusatory — the person may have acted in good faith\n"
            "- End with: 'If you believe this is an error, please contact your HR administrator.'\n"
            "- Start with: 'Your nomination was automatically declined because'"
        )

        response = client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2,
        )
        explanation = response.choices[0].message.content.strip()
        logger.info("LLM fraud explanation generated (%d chars)", len(explanation))
        return explanation

    except Exception as exc:
        logger.warning("LLM explanation failed — using fallback: %s", exc)
        return _FALLBACK_EXPLANATION


# ── Public API ────────────────────────────────────────────────────────────────

def assess(details: dict, tenant_id: int, component_status: dict | None = None) -> dict:
    """
    Run the full fraud assessment for a nomination.

    Args:
        details:   nomination dict from db.get_nomination_details()
        tenant_id: used to select the correct per-tenant RF model

    Returns:
        {
            model_available:   bool,
            fraud_score:       int,
            fraud_prob:        float,
            risk_level:        str,
            warning_flags:     list[str],
            flagged:           bool,
            shap_explanations: list[dict],   # top-5 SHAP contributions; [] if not flagged
            fraud_explanation: str | None,   # LLM text; only set for CRITICAL auto-rejects
        }

    Never raises — on model load failure returns model_available=False and
    the caller routes the nomination as clean.
    SHAP / LLM errors are caught internally; the result always has both keys.
    """
    nomination_id = details.get("nomination_id")
    model_data = _get_model(tenant_id)

    if model_data is None:
        result = {
            "model_available":   False,
            "fraud_score":       0,
            "fraud_prob":        0.0,
            "risk_level":        "NONE",
            "warning_flags":     [],
            "flagged":           False,
            "shap_explanations": [],
            "shap_status":       "SKIPPED",
            "shap_reason":       "model_unavailable",
            "fraud_explanation": None,
            "model_version":     None,
        }
        logger.info(
            "RF SHAP assessment skipped",
            extra={
                "nomination_id": nomination_id,
                "tenant_id": tenant_id,
                "shap_status": "SKIPPED",
                "shap_reason": "model_unavailable",
            },
        )
        result.update(component_availability.unavailable_metadata(
            "RF", "NO_MODEL", component_status, source_missing=True
        ))
        return result

    X_scaled, feature_vals, desc_cosine_sim = _build_features(details, model_data)

    rf    = model_data["p2p_model"]
    proba = rf.predict_proba(X_scaled)
    fraud_prob  = float(proba[0][1]) if proba.shape[1] >= 2 else 0.0
    fraud_score = int(fraud_prob * 100)
    thresholds  = _score_routing_thresholds(tenant_id)
    risk        = _risk_level(fraud_score, thresholds)
    flags       = _warning_flags(model_data, X_scaled, desc_cosine_sim, feature_vals)
    flagged     = risk in ("MEDIUM", "HIGH", "CRITICAL")

    # ── SHAP attribution (flagged nominations only) ───────────────────────────
    shap_explanations: list[dict] = []
    shap_status = "SKIPPED"
    shap_reason: str | None = "risk_below_medium"
    if flagged:
        logger.info(
            "RF SHAP assessment starting",
            extra={
                "nomination_id": nomination_id,
                "tenant_id": tenant_id,
                "fraud_score": fraud_score,
                "risk_level": risk,
            },
        )
        try:
            shap_explanations = _compute_shap(model_data, X_scaled, feature_vals)
            shap_status = "COMPLETED"
            shap_reason = None
            logger.info(
                "RF SHAP assessment completed",
                extra={
                    "nomination_id": nomination_id,
                    "tenant_id": tenant_id,
                    "shap_status": shap_status,
                    "shap_feature_count": len(shap_explanations),
                    "top_features": shap_explanations,
                },
            )
        except Exception as exc:
            shap_status = "FAILED"
            shap_reason = "computation_error"
            logger.warning(
                "RF SHAP assessment failed",
                extra={
                    "nomination_id": nomination_id,
                    "tenant_id": tenant_id,
                    "shap_status": shap_status,
                    "shap_reason": shap_reason,
                    "error": str(exc),
                },
                exc_info=True,
            )
    else:
        logger.info(
            "RF SHAP assessment skipped",
            extra={
                "nomination_id": nomination_id,
                "tenant_id": tenant_id,
                "shap_status": shap_status,
                "shap_reason": shap_reason,
                "fraud_score": fraud_score,
                "risk_level": risk,
            },
        )

    # ── LLM explanation (CRITICAL auto-rejects only) ──────────────────────────
    # MEDIUM / HIGH go to HRBP review — the reviewer writes their own reason.
    # CRITICAL bypasses HRBP, so we need a human-readable explanation for the
    # nominator's rejection notice.
    fraud_explanation: str | None = None
    if risk == "CRITICAL" and shap_explanations:
        fraud_explanation = _generate_explanation(shap_explanations, fraud_score)

    result = {
        "model_available":   True,
        "fraud_score":       fraud_score,
        "fraud_prob":        round(fraud_prob, 4),
        "risk_level":        risk,
        "warning_flags":     flags,
        "flagged":           flagged,
        "shap_explanations": shap_explanations,
        "shap_status":       shap_status,
        "shap_reason":       shap_reason,
        "fraud_explanation": fraud_explanation,
        "model_version":     model_data.get("model_version"),
    }
    result.update(component_availability.available_metadata(component_status))
    return result
