"""
Fraud Detection Integration for FastAPI  —  Multi-Tenant Blob-Direct Edition
=============================================================================

One Random Forest model per tenant is trained by train_fraud_model.py and
stored in Azure Blob Storage as:
    ml-models/random_forest_tenant_1.pkl
    ml-models/random_forest_tenant_2.pkl
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
        """Canonical Random Forest artifact name."""
        return f"random_forest_tenant_{tenant_id}.pkl"

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
        container_name = os.getenv('MODEL_CONTAINER', 'ml-models')

        try:
            from azure.core.exceptions import ResourceNotFoundError

            blob_service = self._blob_service_client()
            blob_name = self._blob_name(tenant_id)
            blob_client = blob_service.get_blob_client(
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
            if isinstance(exc, ResourceNotFoundError):
                logger.warning(
                    "[Tenant %d] RF model blob not found: %s/%s. "
                    "Run train_fraud_model.py to generate it.",
                    tenant_id, container_name, self._blob_name(tenant_id),
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

    # ── Feature engineering and inference moved to auxiliary-service ────────
    # nomination_submitted.py in the auxiliary service owns the full fraud
    # assessment pipeline (feature engineering, embedding, RF inference).
    # The backend retains only model management: loading, caching, eviction,
    # and refresh — used by admin endpoints and the analytics agent.


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

fraud_detector = FraudDetector()


# ============================================================================
# MODULE-LEVEL HELPERS  (used by admin_router, internal_router, main.py)
# ============================================================================

def refresh_model(tenant_id: Optional[int] = None) -> bool:
    """
    Manually re-stream model(s) from blob, updating the in-memory cache
    immediately.

    Pass tenant_id to target a single tenant; omit to refresh all currently
    cached tenants.  Can be called from an admin endpoint without restart.

    Returns True if at least one model was successfully refreshed.
    """
    return fraud_detector.check_for_updates(tenant_id=tenant_id)
