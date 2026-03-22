"""
Fraud Detection Integration for FastAPI  —  Multi-Tenant Edition
=================================================================

One Random Forest model per tenant is trained by train_fraud_model.py and
stored as:
    ml_models/fraud_detection_model_tenant_1.pkl
    ml_models/fraud_detection_model_tenant_2.pkl
    ...

At startup FraudDetector loads all known tenant models.  Inference always
routes to the matching per-tenant model so amount z-scores and behavioural
baselines are never cross-contaminated across currencies/locales.

Tenant IDs to load are discovered from the TENANT_IDS environment variable
(comma-separated, default "1,2").  Add new IDs there as you onboard tenants.
"""

import pickle
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import sqlhelper2 as sqlhelper  # Use sqlhelper2 for database interactions
import os
from pathlib import Path

import logging
logger = logging.getLogger(__name__)  # __name__ will be "fraud_ml"

# ============================================================================
# LOAD ML MODEL  —  per-tenant
# ============================================================================

class FraudDetector:
    """
    Multi-tenant fraud detection wrapper.

    One model is loaded per tenant.  predict_fraud() dispatches to the
    model that matches nomination_data['TenantId'].
    """

    def __init__(self, model_dir: str = 'ml_models'):
        """
        Load all per-tenant models.

        Tenant IDs to discover are read from the TENANT_IDS env var
        (default "1,2").  Each model is loaded from:
            {model_dir}/fraud_detection_model_tenant_{id}.pkl
        If a tenant's model is absent locally it is downloaded from blob.
        """
        self.model_dir = model_dir
        # Dict[tenant_id -> model_data dict or None]
        self.tenant_models: Dict[int, Optional[dict]] = {}

        tenant_ids_env = os.getenv('TENANT_IDS', '1,2')
        tenant_ids = [
            int(t.strip())
            for t in tenant_ids_env.split(',')
            if t.strip().isdigit()
        ]

        logger.info(
            f"🔍 Loading fraud detection models for tenants: {tenant_ids}"
        )
        for tid in tenant_ids:
            self.tenant_models[tid] = self._load_tenant_model(tid)

        loaded = [t for t, m in self.tenant_models.items() if m is not None]
        missing = [t for t, m in self.tenant_models.items() if m is None]
        if loaded:
            logger.info(f"✅ Fraud models loaded for tenants: {loaded}")
        if missing:
            logger.warning(
                f"⚠️  No fraud model available for tenants: {missing}. "
                "Run train_fraud_model.py to generate them."
            )
    
    # ── Path helpers ─────────────────────────────────────────────────────────

    def _local_path(self, tenant_id: int) -> str:
        return os.path.join(
            self.model_dir, f"fraud_detection_model_tenant_{tenant_id}.pkl"
        )

    def _blob_name(self, tenant_id: int) -> str:
        return f"fraud_detection_model_tenant_{tenant_id}.pkl"

    # ── Single-tenant loader ─────────────────────────────────────────────────

    def _load_tenant_model(self, tenant_id: int) -> Optional[dict]:
        """Load (or download) the model for one tenant.  Returns None on failure."""
        local_path = self._local_path(tenant_id)
        blob_name  = self._blob_name(tenant_id)
        try:
            if self._should_update_from_blob(local_path, blob_name):
                logger.info(
                    f"[Tenant {tenant_id}] 📥 Newer model found in blob. Downloading ..."
                )
                return self._download_model_from_blob(local_path, blob_name)
            elif os.path.exists(local_path):
                logger.info(
                    f"[Tenant {tenant_id}] 📂 Loading model from {local_path}"
                )
                with open(local_path, 'rb') as f:
                    return pickle.load(f)
            else:
                logger.info(
                    f"[Tenant {tenant_id}] 📥 No local model — downloading ..."
                )
                return self._download_model_from_blob(local_path, blob_name)
        except FileNotFoundError:
            logger.warning(
                f"[Tenant {tenant_id}] ⚠️  Model not found locally or in blob. "
                "Run train_fraud_model.py first."
            )
            return None
        except Exception as exc:
            logger.error(
                f"[Tenant {tenant_id}] ⚠️  Error loading model: {exc}"
            )
            return None

    # ── Blob helpers (now tenant-parameterised) ──────────────────────────────

    def _should_update_from_blob(self, local_path: str, blob_name: str) -> bool:
        """
        Check if there's a newer version in Azure Blob Storage
        
        Returns:
            True if blob version is newer or local file doesn't exist
            False if local file is up to date
        """
        try:
            # If local file doesn't exist, we need to download
            if not os.path.exists(local_path):
                logger.warning(f"📭 No local model found: {local_path}")
                return True
            else:
                logger.info(f"📂 Local model found: {local_path}")
            
            # Get local file's last modified time
            local_mtime = os.path.getmtime(local_path)
            local_modified = datetime.fromtimestamp(local_mtime, tz=timezone.utc)
            logger.info(f"📅 Local model last modified: {local_modified}")
            
            # Get blob's last modified time
            from azure.storage.blob import BlobServiceClient
            from azure.core.exceptions import ResourceNotFoundError
            
            storage_account = os.getenv('AZURE_STORAGE_ACCOUNT', 'awardnominationmodels')
            storage_key = os.getenv('AZURE_STORAGE_KEY')
            container_name = os.getenv('MODEL_CONTAINER', 'ml-models')
            # blob_name is passed in from _load_tenant_model — do not override here

            # Connect to blob storage
            if storage_key:
                logger.info(f"⚠️  AZURE_STORAGE_KEY found. Using storage account key authentication.")
                connection_string = f"DefaultEndpointsProtocol=https;AccountName={storage_account};AccountKey={storage_key};EndpointSuffix=core.windows.net"
                blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            else:
                logger.warning("⚠️  AZURE_STORAGE_KEY not set. Attempting managed identity ...")
                from azure.identity import DefaultAzureCredential
                account_url = f"https://{storage_account}.blob.core.windows.net"
                credential = DefaultAzureCredential()
                blob_service_client = BlobServiceClient(account_url, credential=credential)

            # Get blob properties
            blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
            
            try:
                properties = blob_client.get_blob_properties()
                blob_modified = properties.last_modified
                logger.info(f"☁️  Blob model last modified: {blob_modified}")
                
                # Compare timestamps
                if blob_modified > local_modified:
                    logger.info(f"🆕 Blob is newer by {(blob_modified - local_modified).total_seconds():.0f} seconds")
                    return True
                else:
                    logger.info(f"✅ Local model is up to date")
                    return False
                    
            except ResourceNotFoundError:
                logger.warning("⚠️  Model not found in blob storage")
                return False
            
        except ImportError:
            logger.warning("⚠️  Azure Storage SDK not installed. Using local model.")
            return False
        except Exception as e:
            logger.error(f"⚠️  Error checking blob version: {e}")
            # If we can't check blob, use local version if it exists
            return False
    def _download_model_from_blob(self, local_path: str, blob_name: str):
        """Download a single tenant model from Azure Blob Storage."""
        try:
            from azure.storage.blob import BlobServiceClient

            storage_account = os.getenv('AZURE_STORAGE_ACCOUNT', 'awardnominationmodels')
            storage_key = os.getenv('AZURE_STORAGE_KEY')
            container_name = os.getenv('MODEL_CONTAINER', 'ml-models')

            logger.info(f"📍 Connecting to: {storage_account}/{container_name}/{blob_name}")
            
            # Option 1: Use storage account key
            if storage_key:
                connection_string = f"DefaultEndpointsProtocol=https;AccountName={storage_account};AccountKey={storage_key};EndpointSuffix=core.windows.net"
                blob_service_client = BlobServiceClient.from_connection_string(connection_string)
                logger.info("🔑 Using storage account key authentication")
            
            # Option 2: Use managed identity (preferred for production)
            else:
                from azure.identity import DefaultAzureCredential
                account_url = f"https://{storage_account}.blob.core.windows.net"
                credential = DefaultAzureCredential()
                blob_service_client = BlobServiceClient(account_url, credential=credential)
                logger.info("🎭 Using managed identity authentication")
            
            # Download the blob
            blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
            
            # Ensure directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Download to local path
            logger.info(f"⬇️  Downloading model to {local_path}")
            with open(local_path, 'wb') as f:
                download_stream = blob_client.download_blob()
                f.write(download_stream.readall())
            
            logger.info(f"✅ Model downloaded from Azure Blob Storage to {local_path}")
            
            # Load the downloaded model
            with open(local_path, 'rb') as f:
                return pickle.load(f)
        
        except ImportError:
            logger.warning("⚠️  Azure Storage SDK not installed. Install with: pip install azure-storage-blob azure-identity")
            raise FileNotFoundError("Could not download model from Azure Blob Storage")
        except Exception as e:
            logger.error(f"❌ Error downloading model from Azure Blob Storage: {e}")
            import traceback
            traceback.print_exc()
            raise FileNotFoundError(f"Could not download model from Azure Blob Storage: {e}")
    
    def check_for_updates(self, tenant_id: Optional[int] = None) -> bool:
        """
        Manually check for and download model updates.

        If tenant_id is None, refreshes all loaded tenants.
        Returns True if at least one model was updated.
        """
        logger.info("🔄 Checking for fraud model updates ...")

        tids = [tenant_id] if tenant_id is not None else list(self.tenant_models.keys())
        updated_any = False

        for tid in tids:
            local_path = self._local_path(tid)
            blob_name  = self._blob_name(tid)
            if self._should_update_from_blob(local_path, blob_name):
                try:
                    model_data = self._download_model_from_blob(local_path, blob_name)
                    self.tenant_models[tid] = model_data
                    logger.info(
                        f"[Tenant {tid}] ✅ Model updated "
                        f"(trained: {model_data['training_date']})"
                    )
                    updated_any = True
                except Exception as exc:
                    logger.error(f"[Tenant {tid}] ❌ Failed to update model: {exc}")
            else:
                logger.info(f"[Tenant {tid}] ✅ Model is already up to date")

        return updated_any

    def calculate_features(
        self,
        nomination_data: Dict[str, Any],
        tenant_model_data: dict,
    ) -> pd.DataFrame:
        """
        Calculate features for a new nomination.

        Args:
            nomination_data: dict with at least:
                - NominatorId, BeneficiaryId, ApproverId
                - Amount  (integer)
                - NominationDate (datetime)
            tenant_model_data: the per-tenant model dict (contains
                amount_mean / amount_std baked in at training time so
                z-scores are never cross-tenant)

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

        # Nominator stats
        if nominator_history:
            nominator_total            = len(nominator_history)
            nominator_amounts          = [row[2] for row in nominator_history]  # Amount col
            nominator_unique_bens      = len(set(row[1] for row in nominator_history))
            nominator_avg_amount       = np.mean(nominator_amounts)
            nominator_std_amount       = np.std(nominator_amounts) if nominator_total > 1 else 0
        else:
            nominator_total = nominator_avg_amount = nominator_std_amount = 0
            nominator_unique_bens = 0

        # Beneficiary stats
        if beneficiary_history:
            beneficiary_total      = len(beneficiary_history)
            beneficiary_amounts    = [row[2] for row in beneficiary_history]
            beneficiary_avg_amount = np.mean(beneficiary_amounts)
        else:
            beneficiary_total = beneficiary_avg_amount = 0

        # Approver stats
        if approver_history:
            approver_total    = len(approver_history)
            approval_times    = [row[1] for row in approver_history if row[1] is not None]
            approver_avg_time = np.mean(approval_times) if approval_times else 24
        else:
            approver_total    = 0
            approver_avg_time = 24

        # Relationship features
        has_reciprocal = sqlhelper.check_reciprocal_nomination(nominator_id, beneficiary_id)
        pair_count     = sqlhelper.get_pair_nomination_count(nominator_id, beneficiary_id)

        # Temporal features
        nomination_date = nomination_data.get('NominationDate', datetime.now())
        day_of_week     = nomination_date.weekday()
        month           = nomination_date.month
        is_weekend      = 1 if day_of_week in [5, 6] else 0

        # ── Amount features (using tenant-scoped mean/std from the model) ────
        amount = nomination_data['Amount']

        # NOTE: amount_mean / amount_std are stored in the model at training
        # time so z-scores reflect that tenant's currency distribution, not
        # a cross-tenant average.
        if overall_mean is not None and overall_std is not None and overall_std > 0:
            amount_zscore = (amount - overall_mean) / overall_std
        else:
            amount_zscore = 0

        is_high_amount      = 1 if amount_zscore > 2 else 0
        concentration_ratio = nominator_total / (nominator_unique_bens + 1)

        # ── Build feature dict ────────────────────────────────────────────────
        features = {
            'Amount':                      amount,
            'DayOfWeek':                   day_of_week,
            'Month':                       month,
            'IsWeekend':                   is_weekend,
            'HoursToApproval':             0,   # unknown at submission time
            'HoursToPayment':              0,   # unknown at submission time
            'NominatorTotalNominations':   nominator_total,
            'NominatorAvgAmount':          nominator_avg_amount,
            'NominatorStdAmount':          nominator_std_amount,
            'NominatorUniqueBeneficiaries': nominator_unique_bens,
            'BeneficiaryTotalReceived':    beneficiary_total,
            'BeneficiaryAvgAmountReceived': beneficiary_avg_amount,
            'ApproverTotalApproved':       approver_total,
            'ApproverAvgApprovalTime':     approver_avg_time,
            'HasReciprocalNomination':     1 if has_reciprocal else 0,
            'PairNominationCount':         pair_count,
            'AmountZScore':                amount_zscore,
            'IsHighAmount':                is_high_amount,
            'IsRapidApproval':             0,   # unknown at submission time
            'NominatorConcentrationRatio': concentration_ratio,
        }

        feature_columns = tenant_model_data['feature_columns']
        return pd.DataFrame([features])[feature_columns]

    def predict_fraud(self, nomination_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict fraud probability for a nomination.

        nomination_data must include 'TenantId' so the call is routed to the
        correct per-tenant model.  All other keys are the same as before
        except 'DollarAmount' has been renamed to 'Amount'.

        Returns:
            Dictionary with fraud prediction results.
        """
        tenant_id     = nomination_data.get('TenantId')
        tenant_model  = self.tenant_models.get(tenant_id) if tenant_id else None

        if tenant_model is None:
            logger.warning(
                f"[Tenant {tenant_id}] No fraud model loaded — returning UNKNOWN. "
                "Run train_fraud_model.py to generate a per-tenant model."
            )
            return {
                'fraud_probability': 0.0,
                'fraud_score': 0,
                'risk_level': 'UNKNOWN',
                'warning_flags': ['No per-tenant model available'],
                'recommendation': 'MANUAL_REVIEW',
            }

        try:
            features_df     = self.calculate_features(nomination_data, tenant_model)
            features_scaled = tenant_model['scaler'].transform(features_df)

            proba = tenant_model['model'].predict_proba(features_scaled)
            # Guard against a single-class model (shouldn't happen after bootstrap fix,
            # but protects inference if an old model is still cached).
            if proba.shape[1] < 2:
                logger.warning(
                    f"[Tenant {tenant_id}] Model only knows one class — "
                    "retrain with train_fraud_model.py to get a proper two-class model."
                )
                fraud_probability = 0.0
            else:
                fraud_probability = proba[0][1]

            # Convert to fraud score (0-100)
            fraud_score = int(fraud_probability * 100)

            # Determine risk level
            if fraud_score >= 80:
                risk_level = 'CRITICAL'
                recommendation = 'BLOCK'
            elif fraud_score >= 60:
                risk_level = 'HIGH'
                recommendation = 'MANUAL_REVIEW'
            elif fraud_score >= 40:
                risk_level = 'MEDIUM'
                recommendation = 'FLAGGED'
            elif fraud_score >= 20:
                risk_level = 'LOW'
                recommendation = 'MONITOR'
            else:
                risk_level = 'NONE'
                recommendation = 'APPROVE'

            # Generate warning flags
            warning_flags = []

            features = features_df.iloc[0]

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
                'fraud_score': fraud_score,
                'risk_level': risk_level,
                'warning_flags': warning_flags,
                'recommendation': recommendation,
                'feature_summary': {
                    'nominator_total_nominations': int(features['NominatorTotalNominations']),
                    'pair_nomination_count': int(features['PairNominationCount']),
                    'has_reciprocal': bool(features['HasReciprocalNomination']),
                    'amount_zscore': round(float(features['AmountZScore']), 2)
                }
            }

        except Exception as e:
            import traceback
            logger.error(
                "Fraud prediction failed — returning UNKNOWN/MANUAL_REVIEW fallback. Error: %s\n%s",
                e, traceback.format_exc()
            )
            return {
                'fraud_probability': 0.0,
                'fraud_score': 0,
                'risk_level': 'UNKNOWN',
                'warning_flags': ['Fraud check error — manual review required'],
                'recommendation': 'MANUAL_REVIEW'
            }

# ============================================================================
# GLOBAL FRAUD DETECTOR INSTANCE
# ============================================================================

# Initialize global fraud detector (loaded once at startup)
fraud_detector = FraudDetector()

# ============================================================================
# FASTAPI ENDPOINT INTEGRATION
# ============================================================================

def get_fraud_assessment(nomination_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get fraud assessment for a nomination.

    nomination_data must include 'TenantId' (int) so the call is routed to
    the correct per-tenant model.  'DollarAmount' has been renamed 'Amount'.

    Usage in main.py:

    @app.post("/api/nominations")
    async def create_nomination(nomination: NominationCreate, ...):
        # Get fraud assessment
        fraud_result = fraud_ml.get_fraud_assessment({
            'TenantId':    tenant_id,                   # ← required for per-tenant model routing
            'NominatorId': effective_user["UserId"],
            'BeneficiaryId': nomination.BeneficiaryId,
            'ApproverId':  manager_id,
            'Amount':      nomination.Amount,           # renamed from DollarAmount
            'NominationDate': datetime.now()
        })
        
        # Log fraud assessment
        print(f"Fraud Assessment: {fraud_result['risk_level']} "
              f"(score: {fraud_result['fraud_score']})")
        
        # Optionally block high-risk nominations
        if fraud_result['risk_level'] == 'CRITICAL':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Nomination blocked due to fraud risk: "
                       f"{', '.join(fraud_result['warning_flags'])}"
            )
        
        # Continue with normal nomination creation...
    """
    return fraud_detector.predict_fraud(nomination_data)

def refresh_model(tenant_id: Optional[int] = None) -> bool:
    """
    Manually refresh per-tenant fraud models from blob storage.

    Pass tenant_id to refresh only one tenant; omit to refresh all.
    Can be called from an admin endpoint to hot-reload models without restart.

    Returns:
        True if at least one model was updated, False otherwise.
    """
    return fraud_detector.check_for_updates(tenant_id=tenant_id)