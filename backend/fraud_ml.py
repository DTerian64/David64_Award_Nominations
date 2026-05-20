"""
Fraud Detection Integration for FastAPI  —  Multi-Tenant Lazy-Load Edition
===========================================================================

One Random Forest model per tenant is trained by train_fraud_model.py and
stored as:
    ml_models/fraud_detection_model_tenant_1.pkl
    ml_models/fraud_detection_model_tenant_2.pkl
    ...

Models are loaded ON DEMAND: the first predict_fraud() call for a given
tenant triggers a load (or blob download).  Models that have not been used
within MODEL_IDLE_TTL_SECONDS (default: 1800 = 30 min) are evicted from
memory by the background loop started in main.py's lifespan handler.

This design is SaaS-friendly: the container never holds memory for tenants
that are not actively using the product, and onboarding a new tenant requires
no restart and no TENANT_IDS config change.

Thread-safety
-------------
- A global cache lock protects reads/writes to the _cache dict.
- A per-tenant load lock prevents "thundering herd" — if two requests arrive
  simultaneously for a tenant that has no cached model, only one of them does
  the actual blob download; the other waits and then reads the now-populated
  cache entry.
"""

from __future__ import annotations

import os
import pickle
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
    model: Optional[dict]               # None = load attempted but failed
    last_used: float = field(default_factory=time.monotonic)


# ============================================================================
# FRAUD DETECTOR  —  lazy-loading, multi-tenant
# ============================================================================

class FraudDetector:
    """
    Multi-tenant fraud detection wrapper with lazy model loading.

    Models are loaded the first time predict_fraud() is called for a tenant
    and evicted from memory after MODEL_IDLE_TTL_SECONDS of inactivity.
    """

    def __init__(
        self,
        model_dir: str = 'ml_models',
        idle_ttl_seconds: Optional[int] = None,
    ):
        self.model_dir = model_dir
        self.idle_ttl: int = idle_ttl_seconds or int(
            os.getenv('MODEL_IDLE_TTL_SECONDS', '1800')
        )

        # tenant_id → _ModelEntry
        self._cache: Dict[int, _ModelEntry] = {}
        self._cache_lock = threading.Lock()

        # Per-tenant locks prevent duplicate loads when concurrent requests
        # arrive for the same tenant before its model is cached.
        self._tenant_load_locks: Dict[int, threading.Lock] = {}
        self._tenant_load_locks_lock = threading.Lock()

        logger.info(
            "FraudDetector initialised (lazy-load mode). "
            "Models load on first request per tenant; "
            "idle TTL = %ds.", self.idle_ttl
        )

    # ── Path helpers ─────────────────────────────────────────────────────────

    def _local_path(self, tenant_id: int) -> str:
        return os.path.join(
            self.model_dir, f"fraud_detection_model_tenant_{tenant_id}.pkl"
        )

    def _blob_name(self, tenant_id: int) -> str:
        return f"fraud_detection_model_tenant_{tenant_id}.pkl"

    # ── Per-tenant load lock ──────────────────────────────────────────────────

    def _get_load_lock(self, tenant_id: int) -> threading.Lock:
        with self._tenant_load_locks_lock:
            if tenant_id not in self._tenant_load_locks:
                self._tenant_load_locks[tenant_id] = threading.Lock()
            return self._tenant_load_locks[tenant_id]

    # ── Public cache API ─────────────────────────────────────────────────────

    def get_model(self, tenant_id: int) -> Optional[dict]:
        """
        Return the cached model for *tenant_id*, loading it lazily if needed.

        Returns None if no model is available (not trained yet / blob missing).
        """
        # ── Fast path: already in cache ──
        with self._cache_lock:
            entry = self._cache.get(tenant_id)
            if entry is not None:
                entry.last_used = time.monotonic()
                return entry.model

        # ── Slow path: need to load — per-tenant lock prevents thundering herd ──
        load_lock = self._get_load_lock(tenant_id)
        with load_lock:
            # Double-check after acquiring: another thread may have loaded while
            # we were waiting on load_lock.
            with self._cache_lock:
                entry = self._cache.get(tenant_id)
                if entry is not None:
                    entry.last_used = time.monotonic()
                    return entry.model

            logger.info("[Tenant %d] Lazy-loading fraud model on first request...", tenant_id)
            model = self._load_tenant_model(tenant_id)

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
            logger.info("Model eviction complete — removed %d tenant model(s): %s", len(to_evict), to_evict)
        return len(to_evict)

    def loaded_tenants(self) -> Dict[int, _ModelEntry]:
        """
        Return a snapshot of currently cached entries keyed by tenant_id.

        Callers must not mutate the returned entries; this is for read-only
        inspection (admin endpoints, logging).
        """
        with self._cache_lock:
            return dict(self._cache)

    # ── Blob helpers ─────────────────────────────────────────────────────────

    def _should_update_from_blob(self, local_path: str, blob_name: str) -> bool:
        """
        Return True if the blob has a newer model than the local file.
        Returns False on any error (fall through to local copy).
        """
        try:
            if not os.path.exists(local_path):
                logger.info("No local model found: %s", local_path)
                return True

            local_mtime = os.path.getmtime(local_path)
            local_modified = datetime.fromtimestamp(local_mtime, tz=timezone.utc)
            logger.info("Local model last modified: %s", local_modified)

            from azure.storage.blob import BlobServiceClient
            from azure.core.exceptions import ResourceNotFoundError

            storage_account = os.getenv('AZURE_STORAGE_ACCOUNT', 'awardnominationmodels')
            storage_key     = os.getenv('AZURE_STORAGE_KEY')
            container_name  = os.getenv('MODEL_CONTAINER', 'ml-models')

            if storage_key:
                conn_str = (
                    f"DefaultEndpointsProtocol=https;"
                    f"AccountName={storage_account};"
                    f"AccountKey={storage_key};"
                    f"EndpointSuffix=core.windows.net"
                )
                blob_service = BlobServiceClient.from_connection_string(conn_str)
            else:
                from azure.identity import DefaultAzureCredential
                blob_service = BlobServiceClient(
                    f"https://{storage_account}.blob.core.windows.net",
                    credential=DefaultAzureCredential(),
                )

            blob_client = blob_service.get_blob_client(container=container_name, blob=blob_name)
            try:
                props = blob_client.get_blob_properties()
                blob_modified = props.last_modified
                if blob_modified > local_modified:
                    logger.info(
                        "Blob is newer by %.0fs — will download.",
                        (blob_modified - local_modified).total_seconds(),
                    )
                    return True
                logger.info("Local model is up to date.")
                return False
            except ResourceNotFoundError:
                logger.warning("Model blob not found in storage: %s", blob_name)
                return False

        except ImportError:
            logger.warning("Azure Storage SDK not installed — using local model.")
            return False
        except Exception as exc:
            logger.error("Error checking blob freshness: %s", exc)
            return False

    def _download_model_from_blob(self, local_path: str, blob_name: str) -> dict:
        """Download and pickle-load one tenant model from Azure Blob Storage."""
        try:
            from azure.storage.blob import BlobServiceClient

            storage_account = os.getenv('AZURE_STORAGE_ACCOUNT', 'awardnominationmodels')
            storage_key     = os.getenv('AZURE_STORAGE_KEY')
            container_name  = os.getenv('MODEL_CONTAINER', 'ml-models')

            if storage_key:
                conn_str = (
                    f"DefaultEndpointsProtocol=https;"
                    f"AccountName={storage_account};"
                    f"AccountKey={storage_key};"
                    f"EndpointSuffix=core.windows.net"
                )
                blob_service = BlobServiceClient.from_connection_string(conn_str)
                logger.info("Using storage account key auth.")
            else:
                from azure.identity import DefaultAzureCredential
                blob_service = BlobServiceClient(
                    f"https://{storage_account}.blob.core.windows.net",
                    credential=DefaultAzureCredential(),
                )
                logger.info("Using managed identity auth.")

            blob_client = blob_service.get_blob_client(container=container_name, blob=blob_name)
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            logger.info("Downloading model to %s ...", local_path)
            with open(local_path, 'wb') as fh:
                fh.write(blob_client.download_blob().readall())

            logger.info("✅ Downloaded: %s", local_path)
            with open(local_path, 'rb') as fh:
                return pickle.load(fh)

        except ImportError:
            logger.warning("Azure Storage SDK not installed.")
            raise FileNotFoundError("Cannot download model — azure-storage-blob not installed.")
        except Exception as exc:
            import traceback
            logger.error("Error downloading model: %s\n%s", exc, traceback.format_exc())
            raise FileNotFoundError(f"Cannot download model from blob: {exc}") from exc

    # ── Single-tenant loader ─────────────────────────────────────────────────

    def _load_tenant_model(self, tenant_id: int) -> Optional[dict]:
        """Load (or download) the model for one tenant. Returns None on failure."""
        local_path = self._local_path(tenant_id)
        blob_name  = self._blob_name(tenant_id)
        try:
            if self._should_update_from_blob(local_path, blob_name):
                logger.info("[Tenant %d] Newer model in blob — downloading...", tenant_id)
                return self._download_model_from_blob(local_path, blob_name)
            elif os.path.exists(local_path):
                logger.info("[Tenant %d] Loading from local: %s", tenant_id, local_path)
                with open(local_path, 'rb') as fh:
                    return pickle.load(fh)
            else:
                logger.info("[Tenant %d] No local model — downloading from blob...", tenant_id)
                return self._download_model_from_blob(local_path, blob_name)
        except FileNotFoundError:
            logger.warning(
                "[Tenant %d] ⚠️  Model not found locally or in blob. "
                "Run train_fraud_model.py to generate it.",
                tenant_id,
            )
            return None
        except Exception as exc:
            logger.error("[Tenant %d] ⚠️  Error loading model: %s", tenant_id, exc)
            return None

    # ── Model refresh (admin / scheduled) ───────────────────────────────────

    def check_for_updates(self, tenant_id: Optional[int] = None) -> bool:
        """
        Check blob for newer model versions and hot-reload if found.

        If *tenant_id* is given, refreshes only that tenant (loading it into
        cache even if it was not previously cached).  If omitted, refreshes
        all tenants currently in the cache.

        Returns True if at least one model was updated.
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
            local_path = self._local_path(tid)
            blob_name  = self._blob_name(tid)
            if self._should_update_from_blob(local_path, blob_name):
                try:
                    model_data = self._download_model_from_blob(local_path, blob_name)
                    with self._cache_lock:
                        if tid in self._cache:
                            self._cache[tid].model     = model_data
                            self._cache[tid].last_used = time.monotonic()
                        else:
                            self._cache[tid] = _ModelEntry(model=model_data)
                    logger.info(
                        "[Tenant %d] ✅ Model updated (%s)",
                        tid, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                    )
                    updated_any = True
                except Exception as exc:
                    logger.error("[Tenant %d] ❌ Failed to update model: %s", tid, exc)
            else:
                logger.info("[Tenant %d] ✅ Model already up to date.", tid)

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

        feature_columns = tenant_model_data['feature_columns']
        return pd.DataFrame([features])[feature_columns]

    # ── Inference ────────────────────────────────────────────────────────────

    def predict_fraud(self, nomination_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict fraud probability for a nomination.

        nomination_data must include 'TenantId' (int).  The model for that
        tenant is loaded lazily on the first call and kept in memory until
        evicted by the idle-TTL background task.

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
    Manually check blob for newer models and hot-reload if found.

    Pass tenant_id to target a single tenant; omit to refresh all currently
    cached tenants.  Can be called from an admin endpoint without restart.

    Returns True if at least one model was updated.
    """
    return fraud_detector.check_for_updates(tenant_id=tenant_id)
