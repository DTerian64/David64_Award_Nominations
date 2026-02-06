"""
Fraud Detection ML Model Training
Trains a machine learning model to detect fraudulent award nominations
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pyodbc
import pickle
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score, roc_curve
import matplotlib.pyplot as plt
import seaborn as sns
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

# ============================================================================
# DATABASE CONNECTION
# ============================================================================

def get_db_connection():
    """Create database connection"""
    connection_string = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.getenv('SQL_SERVER')};"
        f"DATABASE={os.getenv('SQL_DATABASE')};"
        f"UID={os.getenv('SQL_USER')};"
        f"PWD={os.getenv('SQL_PASSWORD')};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
        f"Connection Timeout=30;"
    )
    return pyodbc.connect(connection_string)

# ============================================================================
# FEATURE ENGINEERING
# ============================================================================

def extract_features(df):
    """
    Extract and engineer features for fraud detection
    
    Features include:
    - User behavior patterns
    - Temporal patterns
    - Amount patterns
    - Relationship patterns
    """
    
    print("Extracting features...")
    
    # Convert dates
    df['NominationDate'] = pd.to_datetime(df['NominationDate'])
    df['ApprovedDate'] = pd.to_datetime(df['ApprovedDate'])
    df['PayedDate'] = pd.to_datetime(df['PayedDate'])
    
    # Temporal features
    df['DayOfWeek'] = df['NominationDate'].dt.dayofweek
    df['Month'] = df['NominationDate'].dt.month
    df['Hour'] = df['NominationDate'].dt.hour
    df['IsWeekend'] = df['DayOfWeek'].isin([5, 6]).astype(int)
    
    # Time between events
    df['HoursToApproval'] = (df['ApprovedDate'] - df['NominationDate']).dt.total_seconds() / 3600
    df['HoursToPayment'] = (df['PayedDate'] - df['ApprovedDate']).dt.total_seconds() / 3600
    
    # Replace inf and negative values
    df['HoursToApproval'] = df['HoursToApproval'].replace([np.inf, -np.inf], np.nan)
    df['HoursToPayment'] = df['HoursToPayment'].replace([np.inf, -np.inf], np.nan)
    
    # User behavior features (rolling windows)
    print("Calculating user behavior features...")
    
    # Nominator behavior
    nominator_stats = df.groupby('NominatorId').agg({
        'NominationId': 'count',  # Total nominations by this nominator
        'DollarAmount': ['mean', 'std', 'min', 'max'],
        'BeneficiaryId': 'nunique'  # Unique beneficiaries
    }).reset_index()
    
    nominator_stats.columns = ['NominatorId', 'NominatorTotalNominations', 
                                'NominatorAvgAmount', 'NominatorStdAmount',
                                'NominatorMinAmount', 'NominatorMaxAmount',
                                'NominatorUniqueBeneficiaries']
    
    df = df.merge(nominator_stats, on='NominatorId', how='left')
    
    # Beneficiary behavior (how often they receive nominations)
    beneficiary_stats = df.groupby('BeneficiaryId').agg({
        'NominationId': 'count',
        'DollarAmount': 'mean'
    }).reset_index()
    
    beneficiary_stats.columns = ['BeneficiaryId', 'BeneficiaryTotalReceived', 
                                  'BeneficiaryAvgAmountReceived']
    
    df = df.merge(beneficiary_stats, on='BeneficiaryId', how='left')
    
    # Approver behavior
    approver_stats = df.groupby('ApproverId').agg({
        'NominationId': 'count',
        'HoursToApproval': 'mean'
    }).reset_index()
    
    approver_stats.columns = ['ApproverId', 'ApproverTotalApproved', 'ApproverAvgApprovalTime']
    
    df = df.merge(approver_stats, on='ApproverId', how='left')
    
    # Relationship features
    print("Calculating relationship features...")
    
    # Check for reciprocal nominations
    reciprocal = df.merge(
        df[['NominatorId', 'BeneficiaryId']],
        left_on=['NominatorId', 'BeneficiaryId'],
        right_on=['BeneficiaryId', 'NominatorId'],
        how='inner',
        suffixes=('', '_reciprocal')
    )
    
    df['HasReciprocalNomination'] = df['NominationId'].isin(reciprocal['NominationId']).astype(int)
    
    # Repeated pair nominations (same nominator-beneficiary pair)
    pair_counts = df.groupby(['NominatorId', 'BeneficiaryId']).size().reset_index(name='PairNominationCount')
    df = df.merge(pair_counts, on=['NominatorId', 'BeneficiaryId'], how='left')
    
    # Amount features
    df['AmountZScore'] = (df['DollarAmount'] - df['DollarAmount'].mean()) / df['DollarAmount'].std()
    df['IsHighAmount'] = (df['AmountZScore'] > 2).astype(int)
    df['IsLowAmount'] = (df['AmountZScore'] < -2).astype(int)
    
    # Rapid approval flag
    df['IsRapidApproval'] = (df['HoursToApproval'] < 1).astype(int)
    
    # Concentration ratio (how diverse are nominator's choices)
    df['NominatorConcentrationRatio'] = df['NominatorTotalNominations'] / (df['NominatorUniqueBeneficiaries'] + 1)
    
    print("Feature extraction complete!")
    return df

# ============================================================================
# LOAD AND PREPARE DATA
# ============================================================================

def load_data():
    """Load nomination data and fraud scores from database"""
    print("Loading data from database...")
    
    conn = get_db_connection()
    
    # Load nominations with fraud scores
    query = """
    SELECT 
        n.*,
        fs.FraudScore,
        fs.RiskLevel,
        fs.FraudFlags,
        CASE WHEN fs.RiskLevel IN ('HIGH', 'CRITICAL') THEN 1 ELSE 0 END AS IsFraud
    FROM dbo.Nominations n
    LEFT JOIN dbo.FraudScores fs ON n.NominationId = fs.NominationId
    WHERE n.Status = 'Paid'
    ORDER BY n.NominationDate
    """
    
    df = pd.read_sql(query, conn)
    conn.close()
    
    print(f"Loaded {len(df)} nominations")
    print(f"Fraud cases: {df['IsFraud'].sum()} ({df['IsFraud'].mean()*100:.2f}%)")
    
    return df

# ============================================================================
# TRAIN MODEL
# ============================================================================

def train_model(df):
    """Train fraud detection model"""
    
    # Extract features
    df = extract_features(df)
    
    # Select feature columns
    feature_columns = [
        'DollarAmount',
        'DayOfWeek',
        'Month',
        'IsWeekend',
        'HoursToApproval',
        'HoursToPayment',
        'NominatorTotalNominations',
        'NominatorAvgAmount',
        'NominatorStdAmount',
        'NominatorUniqueBeneficiaries',
        'BeneficiaryTotalReceived',
        'BeneficiaryAvgAmountReceived',
        'ApproverTotalApproved',
        'ApproverAvgApprovalTime',
        'HasReciprocalNomination',
        'PairNominationCount',
        'AmountZScore',
        'IsHighAmount',
        'IsRapidApproval',
        'NominatorConcentrationRatio'
    ]
    
    # Remove rows with missing target
    df_train = df[df['IsFraud'].notna()].copy()
    
    # Prepare features and target
    X = df_train[feature_columns].fillna(0)
    y = df_train['IsFraud']
    
    print(f"\nTraining data shape: {X.shape}")
    print(f"Features: {feature_columns}")
    
    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # Train Random Forest
    print("\nTraining Random Forest model...")
    rf_model = RandomForestClassifier(
        n_estimators=100,
        max_depth=10,
        min_samples_split=20,
        min_samples_leaf=10,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1
    )
    
    rf_model.fit(X_train_scaled, y_train)
    
    # Evaluate
    y_pred = rf_model.predict(X_test_scaled)
    y_pred_proba = rf_model.predict_proba(X_test_scaled)[:, 1]
    
    print("\n" + "="*60)
    print("MODEL EVALUATION - RANDOM FOREST")
    print("="*60)
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=['Legitimate', 'Fraud']))
    
    print("\nConfusion Matrix:")
    cm = confusion_matrix(y_test, y_pred)
    print(cm)
    
    if len(np.unique(y_test)) > 1:
        auc = roc_auc_score(y_test, y_pred_proba)
        print(f"\nROC AUC Score: {auc:.4f}")
    
    # Feature importance
    feature_importance = pd.DataFrame({
        'Feature': feature_columns,
        'Importance': rf_model.feature_importances_
    }).sort_values('Importance', ascending=False)
    
    print("\nTop 10 Most Important Features:")
    print(feature_importance.head(10).to_string(index=False))
    
    # Save model
    model_data = {
        'model': rf_model,
        'scaler': scaler,
        'feature_columns': feature_columns,
        'training_date': datetime.now(),
        'feature_importance': feature_importance
    }
    
    with open('fraud_detection_model.pkl', 'wb') as f:
        pickle.dump(model_data, f)
    
    print("\n✓ Model saved to 'fraud_detection_model.pkl'")
    
    # Create visualizations
    create_visualizations(df, feature_importance, y_test, y_pred_proba)
    
    return model_data

# ============================================================================
# VISUALIZATIONS
# ============================================================================

def create_visualizations(df, feature_importance, y_test, y_pred_proba):
    """Create fraud detection visualizations"""
    
    print("\nCreating visualizations...")
    
    fig, axes = plt.subplots(2, 2, figsize=(15, 12))
    
    # 1. Feature Importance
    axes[0, 0].barh(feature_importance.head(10)['Feature'], 
                     feature_importance.head(10)['Importance'])
    axes[0, 0].set_xlabel('Importance')
    axes[0, 0].set_title('Top 10 Feature Importances')
    axes[0, 0].invert_yaxis()
    
    # 2. Fraud Score Distribution
    fraud_df = df[df['IsFraud'].notna()]
    axes[0, 1].hist([fraud_df[fraud_df['IsFraud']==0]['FraudScore'],
                      fraud_df[fraud_df['IsFraud']==1]['FraudScore']],
                     bins=30, label=['Legitimate', 'Fraud'], alpha=0.7)
    axes[0, 1].set_xlabel('Fraud Score')
    axes[0, 1].set_ylabel('Count')
    axes[0, 1].set_title('Fraud Score Distribution')
    axes[0, 1].legend()
    
    # 3. ROC Curve
    if len(np.unique(y_test)) > 1:
        fpr, tpr, thresholds = roc_curve(y_test, y_pred_proba)
        axes[1, 0].plot(fpr, tpr, label=f'ROC Curve (AUC = {roc_auc_score(y_test, y_pred_proba):.3f})')
        axes[1, 0].plot([0, 1], [0, 1], 'k--', label='Random')
        axes[1, 0].set_xlabel('False Positive Rate')
        axes[1, 0].set_ylabel('True Positive Rate')
        axes[1, 0].set_title('ROC Curve')
        axes[1, 0].legend()
    
    # 4. Fraud by Risk Level
    risk_counts = df[df['RiskLevel'].notna()]['RiskLevel'].value_counts()
    axes[1, 1].bar(risk_counts.index, risk_counts.values)
    axes[1, 1].set_xlabel('Risk Level')
    axes[1, 1].set_ylabel('Count')
    axes[1, 1].set_title('Nominations by Risk Level')
    axes[1, 1].tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    plt.savefig('fraud_detection_analysis.png', dpi=300, bbox_inches='tight')
    print("✓ Visualization saved to 'fraud_detection_analysis.png'")
    
    plt.close()

# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("="*60)
    print("FRAUD DETECTION ML MODEL TRAINING")
    print("="*60)
    
    # Load data
    df = load_data()
    
    # Train model
    model_data = train_model(df)
    
    print("\n" + "="*60)
    print("TRAINING COMPLETE!")
    print("="*60)
    print("\nNext steps:")
    print("1. Review fraud_detection_analysis.png for insights")
    print("2. Integrate fraud_detection_model.pkl into FastAPI")
    print("3. Set up real-time fraud scoring for new nominations")
