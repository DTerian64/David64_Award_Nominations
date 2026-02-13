"""
Fraud Detection Integration for FastAPI
Real-time fraud scoring for new award nominations
"""

import pickle
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, Any
import sqlhelper
import os
from pathlib import Path

import logging
logger = logging.getLogger(__name__)  # __name__ will be "fraud_ml"

# ============================================================================
# LOAD ML MODEL
# ============================================================================

class FraudDetector:
    """Fraud detection model wrapper"""        
    def __init__(self, model_path='ml_models/fraud_detection_model.pkl'):
        """Load the trained model from local path or Azure Blob Storage"""
        logger.info("🔍 Checking for fraud detection model...")
        
        try: 
            # Check if we should update from blob storage
            if self._should_update_from_blob(model_path):
                logger.info("📥 Newer model found in Azure Blob Storage. Downloading...")
                model_data = self._download_model_from_blob(model_path)
            elif os.path.exists(model_path):
                logger.info(f"📂 Loading model from local path: {model_path}")
                with open(model_path, 'rb') as f:
                    model_data = pickle.load(f)
            else:
                logger.info(f"📥 Model not found locally. Downloading from Azure Blob Storage...")
                model_data = self._download_model_from_blob(model_path)
            
            self.model = model_data['model']
            self.scaler = model_data['scaler']
            self.feature_columns = model_data['feature_columns']
            self.training_date = model_data['training_date']
            
            logger.info(f"✅ Fraud detection model loaded (trained: {self.training_date})")
            
        except FileNotFoundError:
            logger.error("⚠️  Fraud detection model not found. Please run train_fraud_model.py first.")
            self.model = None
        except Exception as e:
            logger.error(f"⚠️  Error loading fraud detection model: {e}")
            import traceback
            traceback.print_exc()
            self.model = None
    
    def _should_update_from_blob(self, local_path: str) -> bool:
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
            blob_name = os.getenv('MODEL_BLOB_NAME', 'fraud_detection_model.pkl')
            
            # Connect to blob storage
            if storage_key:
                logger.info(f"⚠️  AZURE_STORAGE_KEY is found {storage_key}. Using storage account key authentication.")   
                connection_string = f"DefaultEndpointsProtocol=https;AccountName={storage_account};AccountKey={storage_key};EndpointSuffix=core.windows.net"
                blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            else:
                logger.warning("⚠️  AZURE_STORAGE_KEY not set. Attempting to use managed identity or other auth method.")   
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
    def _download_model_from_blob(self, local_path: str):
        """Download model from Azure Blob Storage"""
        try:
            from azure.storage.blob import BlobServiceClient
            
            # Get credentials from environment variables
            storage_account = os.getenv('AZURE_STORAGE_ACCOUNT', 'awardnominationmodels')
            storage_key = os.getenv('AZURE_STORAGE_KEY')
            container_name = os.getenv('MODEL_CONTAINER', 'ml-models')
            blob_name = os.getenv('MODEL_BLOB_NAME', 'fraud_detection_model.pkl')
            
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
    
    def check_for_updates(self, model_path='ml_models/fraud_detection_model.pkl') -> bool:
        """
        Manually check for and download model updates
        
        Returns:
            True if model was updated, False otherwise
        """
        logger.info("\n🔄 Manually checking for model updates...")
        
        if self._should_update_from_blob(model_path):
            try:
                logger.info("📥 Downloading updated model...")
                model_data = self._download_model_from_blob(model_path)
                
                # Update the loaded model
                self.model = model_data['model']
                self.scaler = model_data['scaler']
                self.feature_columns = model_data['feature_columns']
                self.training_date = model_data['training_date']
                
                logger.info(f"✅ Model updated successfully (trained: {self.training_date})")
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to update model: {e}")
                return False
        else:
            logger.info("✅ Model is already up to date")
            return False
      
    def calculate_features(self, nomination_data: Dict[str, Any]) -> pd.DataFrame:
        """
        Calculate features for a new nomination
        
        Args:
            nomination_data: Dictionary with nomination details
                - NominatorId
                - BeneficiaryId
                - ApproverId
                - DollarAmount
                - NominationDate (datetime)
        
        Returns:
            DataFrame with calculated features
        """
        
        # Get historical data for feature calculation
        nominator_id = nomination_data['NominatorId']
        beneficiary_id = nomination_data['BeneficiaryId']
        approver_id = nomination_data['ApproverId']
        
        # Query historical nominations for this nominator
        nominator_history = sqlhelper.get_nominator_history(nominator_id)
        beneficiary_history = sqlhelper.get_beneficiary_history(beneficiary_id)
        approver_history = sqlhelper.get_approver_history(approver_id)
        
        # Calculate nominator features
        if nominator_history:
            nominator_total = len(nominator_history)
            nominator_amounts = [row[2] for row in nominator_history]  # DollarAmount
            nominator_beneficiaries = set([row[1] for row in nominator_history])  # Unique beneficiaries
            
            nominator_avg_amount = np.mean(nominator_amounts) if nominator_amounts else 0
            nominator_std_amount = np.std(nominator_amounts) if len(nominator_amounts) > 1 else 0
            nominator_min_amount = min(nominator_amounts) if nominator_amounts else 0
            nominator_max_amount = max(nominator_amounts) if nominator_amounts else 0
            nominator_unique_beneficiaries = len(nominator_beneficiaries)
        else:
            nominator_total = 0
            nominator_avg_amount = 0
            nominator_std_amount = 0
            nominator_min_amount = 0
            nominator_max_amount = 0
            nominator_unique_beneficiaries = 0
        
        # Calculate beneficiary features
        if beneficiary_history:
            beneficiary_total = len(beneficiary_history)
            beneficiary_amounts = [row[2] for row in beneficiary_history]
            beneficiary_avg_amount = np.mean(beneficiary_amounts) if beneficiary_amounts else 0
        else:
            beneficiary_total = 0
            beneficiary_avg_amount = 0
        
        # Calculate approver features
        if approver_history:
            approver_total = len(approver_history)
            # Calculate average approval time if we have the data
            approver_avg_time = 24  # Default 24 hours
        else:
            approver_total = 0
            approver_avg_time = 24
        
        # Check for reciprocal nominations
        has_reciprocal = sqlhelper.check_reciprocal_nomination(nominator_id, beneficiary_id)
        
        # Check pair nomination count
        pair_count = sqlhelper.get_pair_nomination_count(nominator_id, beneficiary_id)
        
        # Calculate temporal features
        nomination_date = nomination_data.get('NominationDate', datetime.now())
        day_of_week = nomination_date.weekday()
        month = nomination_date.month
        is_weekend = 1 if day_of_week in [5, 6] else 0
        
        # Amount features
        dollar_amount = nomination_data['DollarAmount']
        
        # Get overall statistics for z-score
        overall_stats = sqlhelper.get_overall_amount_stats()
        if overall_stats:
            overall_mean = overall_stats[0]
            overall_std = overall_stats[1]
            amount_zscore = (dollar_amount - overall_mean) / overall_std if overall_std > 0 else 0
        else:
            amount_zscore = 0
        
        is_high_amount = 1 if amount_zscore > 2 else 0
        
        # Concentration ratio
        concentration_ratio = nominator_total / (nominator_unique_beneficiaries + 1)
        
        # Create feature dictionary
        features = {
            'DollarAmount': dollar_amount,
            'DayOfWeek': day_of_week,
            'Month': month,
            'IsWeekend': is_weekend,
            'HoursToApproval': 0,  # Unknown for new nominations
            'HoursToPayment': 0,   # Unknown for new nominations
            'NominatorTotalNominations': nominator_total,
            'NominatorAvgAmount': nominator_avg_amount,
            'NominatorStdAmount': nominator_std_amount,
            'NominatorUniqueBeneficiaries': nominator_unique_beneficiaries,
            'BeneficiaryTotalReceived': beneficiary_total,
            'BeneficiaryAvgAmountReceived': beneficiary_avg_amount,
            'ApproverTotalApproved': approver_total,
            'ApproverAvgApprovalTime': approver_avg_time,
            'HasReciprocalNomination': 1 if has_reciprocal else 0,
            'PairNominationCount': pair_count,
            'AmountZScore': amount_zscore,
            'IsHighAmount': is_high_amount,
            'IsRapidApproval': 0,  # Unknown for new nominations
            'NominatorConcentrationRatio': concentration_ratio
        }
        
        # Create DataFrame with correct column order
        df = pd.DataFrame([features])[self.feature_columns]
        
        return df
    
    def predict_fraud(self, nomination_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict fraud probability for a nomination
        
        Args:
            nomination_data: Dictionary with nomination details
        
        Returns:
            Dictionary with fraud prediction results
        """
        
        if self.model is None:
            return {
                'fraud_probability': 0.0,
                'fraud_score': 0,
                'risk_level': 'UNKNOWN',
                'warning_flags': ['Model not loaded'],
                'recommendation': 'MANUAL_REVIEW'
            }
        
        # Calculate features
        features_df = self.calculate_features(nomination_data)
        
        # Scale features
        features_scaled = self.scaler.transform(features_df)
        
        # Predict
        fraud_probability = self.model.predict_proba(features_scaled)[0][1]
        fraud_prediction = self.model.predict(features_scaled)[0]
        
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
    Get fraud assessment for a nomination
    
    This function should be called in your FastAPI endpoint before
    creating a new nomination.
    
    Usage in main.py:
    
    @app.post("/api/nominations/create")
    async def create_nomination(nomination: NominationCreate, ...):
        # Get fraud assessment
        fraud_result = fraud_ml.get_fraud_assessment({
            'NominatorId': effective_user["UserId"],
            'BeneficiaryId': nomination.BeneficiaryId,
            'ApproverId': manager_id,
            'DollarAmount': nomination.DollarAmount,
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

def refresh_model() -> bool:
    """
    Manually refresh the fraud detection model from blob storage
    
    Can be called from an admin endpoint to force a model update
    
    Returns:
        True if model was updated, False otherwise
    """
    return fraud_detector.check_for_updates()