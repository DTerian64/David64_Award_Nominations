"""
Fraud Detection Integration for FastAPI  —  Multi-Tenant Blob-Direct Edition
=============================================================================

One Random Forest model per tenant is trained by train_fraud_model.py and
stored in Azure Blob Storage as:
    ml-models/fraud_detection_model_tenant_1.pkl
    ml-models/fraud_detection_model_tenant_2.pkl
    ...

Models are loaded ON DEMAND: the first predict_fraud() call for a given tenant
streams the pkl DIRECTLY from blob into memory (pickle.loads(bytes)) — no local
copy is written to disk.  Models that have not been used within
MODEL_IDLE_TTL_SECONDS (default: 1800 = 30 min) are evicted from memory by the
background loop started in main.py's lifespan handler.

Freshness — how weekly retraining propagates automatically
----------------------------------------------------------
The fraud-analytics-job uploads a new pkl to blob storage every Monday.
Because there is no local copy:
  • The cached model is evicted after MODEL_IDLE_TTL_SECONDS of inactivity.
  • The next predict_fraud() call after eviction streams the fresh blob.
  • Net lag = at most one TTL period after the upload completes.

This design is SaaS-friendly: no restarts, no TENANT_IDS config, no disk I/O.
Onboarding a new tenant requires only a trained pkl in blob; the backend picks
it up automatically on the first request for that tenant.

Thread-safety
-------------
- A global cache lock protects reads/writes to the _cache dict.
- A per-tenant load lock prevents "thundering herd" — if two requests arrive
  simultaneously for a tenant with no cached model, only one does the blob
  download; the other waits and reads the now-populated cache entry.
"""

from __future__ import annotations

import os
import pickle
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import numpy as np
import pandas as pd

import sqlhelper2 as sqlhelper
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# MODEL CACHE ENTRY
# ============================================================================

@dataclass
class _ModelEntry:
    """One slot in the lazy-load cache for a single tenant."""
    model: Optional[dict]               # None = load attempted but blob missing
    last_used: float = field(default_factory=time.monotonic)


# ============================================================================
# FRAUD DETECTOR  —  lazy-loading, multi-tenant, blob-direct
# ============================================================================

class FraudDetector:
    """
    Multi-tenant fraud detection wrapper with lazy, blob-direct model loading.

    Models are streamed from Azure Blob Storage on the first predict_fraud()
    call for a tenant and evicted from memory after MODEL_IDLE_TTL_SECONDS of
    inactivity.  No pkl file is ever written to local disk.
    """

    def __init__(self, idle_ttl_seconds: Optional[int] = None):
        self.idle_ttl: int = idle_ttl_seconds or int(
            os.getenv('MODEL_IDLE_TTL_SECONDS', '1800')
        )

        # tenant_id → _ModelEntry
        self._cache: Dict[int, _ModelEntry] = {}
        self._cache_lock = threading.Lock()

        # Per-tenant locks prevent duplicate blob downloads when concurrent
        # requests arrive for the same tenant before its model is cached.
        self._tenant_load_locks: Dict[int, threading.Lock] = {}
        self._tenant_load_locks_lock = threading.Lock()

        logger.info(
            "FraudDetector initialised (blob-direct lazy-load mode). "
            "Models stream from blob on first request per tenant; "
            "idle TTL = %ds.", self.idle_ttl
        )

    # ── Blob name helper ─────────────────────────────────────────────────────

    @staticmethod
    def _blob_name(tenant_id: int) -> str:
        return f"fraud_detection_model_tenant_{tenant_id}.pkl"

    # ── Blob client factory ──────────────────────────────────────────────────

    @staticmethod
    def _blob_service_client():
        """
        Return an authenticated BlobServiceClient.

        Auth priority:
          1. AZURE_STORAGE_KEY env var  → AccountKey connection string
          2. Fallback                   → DefaultAzureCredential (Managed Identity)
        """
        from azure.storage.blob import BlobServiceClient

        storage_account = os.getenv('AZURE_STORAGE_ACCOUNT', 'awardnominationmodels')
        storage_key     = os.getenv('AZURE_STORAGE_KEY')

        if storage_key:
            conn_str = (
                f"DefaultEndpointsProtocol=https;"
                f"AccountName={storage_account};"
                f"AccountKey={storage_key};"
                f"EndpointSuffix=core.windows.net"
            )
            logger.debug("BlobServiceClient: using storage account key auth.")
            return BlobServiceClient.from_connection_string(conn_str)

        from azure.identity import DefaultAzureCredential
        logger.debug("BlobServiceClient: using DefaultAzureCredential (Managed Identity).")
        return BlobServiceClient(
            f"https://{storage_account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )

    # ── Blob-direct model streaming ──────────────────────────────────────────

    def _stream_from_blob(self, tenant_id: int) -> Optional[dict]:
        """
        Download the pkl for *tenant_id* directly from blob storage into memory
        and deserialise it with pickle.loads().  No file is written to disk.

        Returns None if the blob does not exist or on any error.
        """
        blob_name      = self._blob_name(tenant_id)
        container_name = os.getenv('MODEL_CONTAINER', 'ml-models')

        try:
            blob_service = self._blob_service_client()
            blob_client  = blob_service.get_blob_client(
                container=container_name, blob=blob_name
            )

            logger.info(
                "[Tenant %d] Streaming model from blob: %s/%s …",
                tenant_id, container_name, blob_name,
            )
            data = blob_client.download_blob().readall()
            model_data = pickle.loads(data)
            logger.info(
                "[Tenant %d] ✅ Model streamed from blob (%d bytes).",
                tenant_id, len(data),
            )
            return model_data

        except ImportError:
            logger.error(
                "[Tenant %d] azure-storage-blob not installed — cannot load model.",
                tenant_id,
            )
            return None
        except Exception as exc:
            from azure.core.exceptions import ResourceNotFoundError
            if isinstance(exc, ResourceNotFoundError):
                logger.warning(
                    "[Tenant %d] Model blob not found: %s/%s. "
                    "Run train_fraud_model.py to generate it.",
                    tenant_id, container_name, blob_name,
                )
            else:
                import traceback
                logger.error(
                    "[Tenant %d] Error streaming model: %s\n%s",
                    tenant_id, exc, traceback.format_exc(),
                )
            return None

    # ── Per-tenant load lock ──────────────────────────────────────────────────

    def _get_load_lock(self, tenant_id: int) -> threading.Lock:
        with self._tenant_load_locks_lock:
            if tenant_id not in self._tenant_load_locks:
                self._tenant_load_locks[tenant_id] = threading.Lock()
            return self._tenant_load_locks[tenant_id]

    # ── Public cache API ─────────────────────────────────────────────────────

    def get_model(self, tenant_id: int) -> Optional[dict]:
        """
        Return the cached model for *tenant_id*, streaming it from blob
        lazily if needed.

        Returns None if no model is available (not trained yet / blob missing).
        """
        # ── Fast path: already in cache ──
        with self._cache_lock:
            entry = self._cache.get(tenant_id)
            if entry is not None:
                entry.last_used = time.monotonic()
                return entry.model

        # ── Slow path: stream from blob — per-tenant lock prevents thundering herd ──
        load_lock = self._get_load_lock(tenant_id)
        with load_lock:
            # Double-check after acquiring: another thread may have streamed
            # while we were waiting on load_lock.
            with self._cache_lock:
                entry = self._cache.get(tenant_id)
                if entry is not None:
                    entry.last_used = time.monotonic()
                    return entry.model

            logger.info("[Tenant %d] Cache miss — streaming model from blob…", tenant_id)
            model = self._stream_from_blob(tenant_id)

            with self._cache_lock:
                self._cache[tenant_id] = _ModelEntry(model=model)

            if model is not None:
                logger.info("[Tenant %d] ✅ Model loaded and cached.", tenant_id)
            else:
                logger.warning(
                    "[Tenant %d] ⚠️  Model unavailable — returning None. "
                    "Run train_fraud_model.py to generate a per-tenant model.",
                    tenant_id,
                )
            return model

    def evict_idle(self) -> int:
        """
        Evict models that have been idle longer than *self.idle_ttl* seconds.

        Called periodically by the background loop in main.py.
        Returns the number of models evicted.
        """
        now = time.monotonic()
        with self._cache_lock:
            to_evict = [
                tid for tid, entry in self._cache.items()
                if (now - entry.last_used) > self.idle_ttl
            ]
            for tid in to_evict:
                logger.info(
                    "[Tenant %d] Evicting idle fraud model (idle > %ds).",
                    tid, self.idle_ttl,
                )
                del self._cache[tid]
        if to_evict:
            logger.info(
                "Model eviction complete — removed %d tenant model(s): %s",
                len(to_evict), to_evict,
            )
        return len(to_evict)

    def loaded_tenants(self) -> Dict[int, _ModelEntry]:
        """
        Return a snapshot of currently cached entries keyed by tenant_id.

        Callers must not mutate the returned entries; this is for read-only
        inspection (admin endpoints, logging).
        """
        with self._cache_lock:
            return dict(self._cache)

    # ── Model refresh (admin / scheduled) ───────────────────────────────────

    def check_for_updates(self, tenant_id: Optional[int] = None) -> bool:
        """
        Eagerly re-stream model(s) from blob, replacing the in-memory cache.

        If *tenant_id* is given, refreshes only that tenant (loading it into
        cache even if it was not previously cached).  If omitted, refreshes
        all tenants currently in the cache.

        Unlike the TTL eviction path, this immediately updates the cached entry
        so that admin endpoints report the new training_date right away.

        Returns True if at least one model was successfully refreshed.
        """
        with self._cache_lock:
            if tenant_id is not None:
                tids = [tenant_id]
            else:
                tids = list(self._cache.keys())

        if not tids:
            logger.info("check_for_updates: no models currently cached — nothing to refresh.")
            return False

        updated_any = False
        for tid in tids:
            logger.info("[Tenant %d] Forcing re-stream from blob…", tid)
            model_data = self._stream_from_blob(tid)
            if model_data is not None:
                with self._cache_lock:
                    if tid in self._cache:
                        self._cache[tid].model     = model_data
                        self._cache[tid].last_used = time.monotonic()
                    else:
                        self._cache[tid] = _ModelEntry(model=model_data)
                logger.info(
                    "[Tenant %d] ✅ Model refreshed (%s)",
                    tid, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                )
                updated_any = True
            else:
                logger.error("[Tenant %d] ❌ Failed to refresh model — blob stream returned None.", tid)

        return updated_any

    # ── Feature engineering ──────────────────────────────────────────────────

    def calculate_features(
        self,
        nomination_data: Dict[str, Any],
        tenant_model_data: dict,
    ) -> pd.DataFrame:
        """
        Build the feature vector for one nomination.

        Args:
            nomination_data: dict with at least NominatorId, BeneficiaryId,
                ApproverId, Amount (int), NominationDate (datetime).
            tenant_model_data: the per-tenant model dict (contains
                amount_mean / amount_std baked in at training time so
                z-scores are never cross-tenant).

        Returns:
            Single-row DataFrame aligned to the model's feature_columns.
        """
        nominator_id   = nomination_data['NominatorId']
        beneficiary_id = nomination_data['BeneficiaryId']
        approver_id    = nomination_data['ApproverId']

        # ── Historical lookups ────────────────────────────────────────────────
        nominator_history   = sqlhelper.get_nominator_history(nominator_id)
        beneficiary_history = sqlhelper.get_beneficiary_history(beneficiary_id)
        approver_history    = sqlhelper.get_approver_history(approver_id)

        if nominator_history:
            nominator_total       = len(nominator_history)
            nominator_amounts     = [row[2] for row in nominator_history]
            nominator_unique_bens = len(set(row[1] for row in nominator_history))
            nominator_avg_amount  = np.mean(nominator_amounts)
            nominator_std_amount  = np.std(nominator_amounts) if nominator_total > 1 else 0
        else:
            nominator_total = nominator_avg_amount = nominator_std_amount = 0
            nominator_unique_bens = 0

        if beneficiary_history:
            beneficiary_total      = len(beneficiary_history)
            beneficiary_amounts    = [row[2] for row in beneficiary_history]
            beneficiary_avg_amount = np.mean(beneficiary_amounts)
        else:
            beneficiary_total = beneficiary_avg_amount = 0

        if approver_history:
            approver_total  = len(approver_history)
            approval_times  = [row[1] for row in approver_history if row[1] is not None]
            approver_avg_time = np.mean(approval_times) if approval_times else 24
        else:
            approver_total    = 0
            approver_avg_time = 24

        has_reciprocal = sqlhelper.check_reciprocal_nomination(nominator_id, beneficiary_id)
        pair_count     = sqlhelper.get_pair_nomination_count(nominator_id, beneficiary_id)

        nomination_date = nomination_data.get('NominationDate', datetime.now())
        day_of_week     = nomination_date.weekday()
        month           = nomination_date.month
        is_weekend      = 1 if day_of_week in [5, 6] else 0

        # ── Amount z-score using tenant-scoped stats from training ────────────
        # amount_mean / amount_std are stored in the pickle at training time so
        # inference never mixes currencies or pay bands across tenants.
        amount       = nomination_data['Amount']
        overall_mean = tenant_model_data.get('amount_mean')
        overall_std  = tenant_model_data.get('amount_std')

        if overall_mean is not None and overall_std is not None and overall_std > 0:
            amount_zscore = (amount - overall_mean) / overall_std
        else:
            amount_zscore = 0

        is_high_amount      = 1 if amount_zscore > 2 else 0
        concentration_ratio = nominator_total / (nominator_unique_bens + 1)

        features = {
            'Amount':                       amount,
            'DayOfWeek':                    day_of_week,
            'Month':                        month,
            'IsWeekend':                    is_weekend,
            'HoursToApproval':              0,   # unknown at submission time
            'HoursToPayment':               0,   # unknown at submission time
            'NominatorTotalNominations':    nominator_total,
            'NominatorAvgAmount':           nominator_avg_amount,
            'NominatorStdAmount':           nominator_std_amount,
            'NominatorUniqueBeneficiaries': nominator_unique_bens,
            'BeneficiaryTotalReceived':     beneficiary_total,
            'BeneficiaryAvgAmountReceived': beneficiary_avg_amount,
            'ApproverTotalApproved':        approver_total,
            'ApproverAvgApprovalTime':      approver_avg_time,
            'HasReciprocalNomination':      1 if has_reciprocal else 0,
            'PairNominationCount':          pair_count,
            'AmountZScore':                 amount_zscore,
            'IsHighAmount':                 is_high_amount,
            'IsRapidApproval':              0,   # unknown at submission time
            'NominatorConcentrationRatio':  concentration_ratio,
        }

        # ── Nomination category — target-encoded fraud rate ──────────────────
        # Look up the mean fraud rate for this CategoryId from the encoding map
        # stored in the pkl at training time.  Unknown or absent categories fall
        # back to the global fraud rate, so adding/removing tenant categories
        # never breaks the feature vector.
        category_id          = nomination_data.get('CategoryId')
        category_fraud_rate  = tenant_model_data.get('category_fraud_rate', {})
        global_fraud_rate    = tenant_model_data.get('global_fraud_rate', 0.0)
        features['CategoryFraudRate'] = (
            category_fraud_rate.get(category_id, global_fraud_rate)
            if category_id is not None
            else 0.0
        )

        feature_columns = tenant_model_data['feature_columns']

        feature_df = pd.DataFrame([features])[feature_columns]

        # ── Diagnostic: log the full feature vector ───────────────────────────
        # Baked directly into the message string so _AppLogFilter / the OTel
        # exporter cannot drop them.  Remove once investigation is complete.
        logger.info(
            "[Tenant %s] Fraud feature vector nominator=%s → beneficiary=%s | "
            "Amount=%s AmountZScore=%s IsHighAmount=%s PairNominationCount=%s "
            "HasReciprocal=%s NominatorTotal=%s NominatorUniqueBens=%s "
            "ConcentrationRatio=%s NominatorAvgAmt=%s NominatorStdAmt=%s "
            "BeneficiaryTotalReceived=%s BeneficiaryAvgAmt=%s "
            "ApproverTotalApproved=%s ApproverAvgApprovalTime=%s "
            "IsWeekend=%s IsRapidApproval=%s "
            "CategoryFraudRate=%s "
            "pkl_amount_mean=%s pkl_amount_std=%s",
            nomination_data.get('TenantId'),
            nomination_data.get('NominatorId'),
            nomination_data.get('BeneficiaryId'),
            features.get('Amount'),
            round(features.get('AmountZScore', 0), 3),
            features.get('IsHighAmount'),
            features.get('PairNominationCount'),
            features.get('HasReciprocalNomination'),
            features.get('NominatorTotalNominations'),
            features.get('NominatorUniqueBeneficiaries'),
            round(features.get('NominatorConcentrationRatio', 0), 3),
            round(features.get('NominatorAvgAmount', 0), 2),
            round(features.get('NominatorStdAmount', 0), 2),
            features.get('BeneficiaryTotalReceived'),
            round(features.get('BeneficiaryAvgAmountReceived', 0), 2),
            features.get('ApproverTotalApproved'),
            round(features.get('ApproverAvgApprovalTime', 0), 2),
            features.get('IsWeekend'),
            features.get('IsRapidApproval'),
            round(features.get('CategoryFraudRate', 0), 4),
            round(tenant_model_data.get('amount_mean', 0), 2),
            round(tenant_model_data.get('amount_std', 0), 2),
        )

        return feature_df

    # ── Inference ────────────────────────────────────────────────────────────

    def predict_fraud(self, nomination_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict fraud probability for a nomination.

        nomination_data must include 'TenantId' (int).  The model for that
        tenant is streamed from blob lazily on the first call and kept in
        memory until evicted by the idle-TTL background task.

        Returns a dict with fraud_probability, fraud_score, risk_level,
        warning_flags, and recommendation.
        """
        tenant_id    = nomination_data.get('TenantId')
        tenant_model = self.get_model(tenant_id) if tenant_id is not None else None

        if tenant_model is None:
            logger.warning(
                "[Tenant %s] No fraud model available — returning UNKNOWN. "
                "Run train_fraud_model.py to generate a per-tenant model.",
                tenant_id,
            )
            return {
                'fraud_probability': 0.0,
                'fraud_score':       0,
                'risk_level':        'UNKNOWN',
                'warning_flags':     ['No per-tenant model available'],
                'recommendation':    'MANUAL_REVIEW',
            }

        try:
            features_df     = self.calculate_features(nomination_data, tenant_model)
            features_scaled = tenant_model['scaler'].transform(features_df)

            proba = tenant_model['model'].predict_proba(features_scaled)
            if proba.shape[1] < 2:
                logger.warning(
                    "[Tenant %d] Model has only one class — retrain with "
                    "train_fraud_model.py.", tenant_id,
                )
                fraud_probability = 0.0
            else:
                fraud_probability = proba[0][1]

            fraud_score = int(fraud_probability * 100)

            if fraud_score >= 80:
                risk_level     = 'CRITICAL'
                recommendation = 'BLOCK'
            elif fraud_score >= 60:
                risk_level     = 'HIGH'
                recommendation = 'MANUAL_REVIEW'
            elif fraud_score >= 40:
                risk_level     = 'MEDIUM'
                recommendation = 'FLAGGED'
            elif fraud_score >= 20:
                risk_level     = 'LOW'
                recommendation = 'MONITOR'
            else:
                risk_level     = 'NONE'
                recommendation = 'APPROVE'

            warning_flags = []
            features      = features_df.iloc[0]

            if features['NominatorTotalNominations'] > 50:
                warning_flags.append('High frequency nominator')
            if features['PairNominationCount'] > 5:
                warning_flags.append('Repeated beneficiary')
            if features['HasReciprocalNomination'] == 1:
                warning_flags.append('Reciprocal nomination detected')
            if features['IsHighAmount'] == 1:
                warning_flags.append('Unusually high amount')
            if features['NominatorConcentrationRatio'] > 5:
                warning_flags.append('Limited beneficiary diversity')

            return {
                'fraud_probability': round(fraud_probability, 4),
                'fraud_score':       fraud_score,
                'risk_level':        risk_level,
                'warning_flags':     warning_flags,
                'recommendation':    recommendation,
                'feature_summary': {
                    'nominator_total_nominations': int(features['NominatorTotalNominations']),
                    'pair_nomination_count':       int(features['PairNominationCount']),
                    'has_reciprocal':              bool(features['HasReciprocalNomination']),
                    'amount_zscore':               round(float(features['AmountZScore']), 2),
                },
            }

        except Exception as exc:
            import traceback
            logger.error(
                "Fraud prediction failed — returning UNKNOWN/MANUAL_REVIEW fallback. "
                "Error: %s\n%s", exc, traceback.format_exc(),
            )
            return {
                'fraud_probability': 0.0,
                'fraud_score':       0,
                'risk_level':        'UNKNOWN',
                'warning_flags':     ['Fraud check error — manual review required'],
                'recommendation':    'MANUAL_REVIEW',
            }


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

fraud_detector = FraudDetector()


# ============================================================================
# MODULE-LEVEL HELPERS  (used by main.py)
# ============================================================================

def get_fraud_assessment(nomination_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Thin wrapper kept for backward compatibility with main.py call sites.

    nomination_data must include 'TenantId' (int).
    """
    return fraud_detector.predict_fraud(nomination_data)


def refresh_model(tenant_id: Optional[int] = None) -> bool:
    """
    Manually re-stream model(s) from blob, updating the in-memory cache
    immediately.

    Pass tenant_id to target a single tenant; omit to refresh all currently
    cached tenants.  Can be called from an admin endpoint without restart.

    Returns True if at least one model was successfully refreshed.
    """
    return fraud_detector.check_for_updates(tenant_id=tenant_id)
