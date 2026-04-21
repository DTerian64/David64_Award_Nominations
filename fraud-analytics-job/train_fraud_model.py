"""
Fraud Detection ML Model Training  —  Multi-Tenant Edition
===========================================================

Trains one Random Forest model per tenant and saves each to its own pickle:
    Output/fraud_detection_model_tenant_1.pkl
    Output/fraud_detection_model_tenant_2.pkl
    ...

Why separate files?
    - Amounts differ by orders of magnitude across currencies (USD vs KRW).
      A shared model would make every KRW nomination look like an extreme
      outlier against a USD mean — corrupting the AmountZScore feature.
    - Fraud behavioural baselines (velocity, org patterns) differ by tenant.
    - Models can be retrained and uploaded independently without touching
      production scoring for other tenants.

After training, upload each .pkl from Output/ to Azure Blob Storage under
the same filename so the backend FraudDetector picks them up on next restart.
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime
import pyodbc
import pickle
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score, roc_curve
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

# ── Blob Storage upload helper ─────────────────────────────────────────────────

def _upload_artefact(local_path: Path) -> None:
    """
    Upload a local file to Azure Blob Storage and keep it under the same
    filename (no path prefix).  Uses the User-Assigned Managed Identity
    injected via AZURE_CLIENT_ID; falls back to env-var key auth when
    running locally with AZURE_STORAGE_KEY set.

    Env vars (set by Terraform / Container Apps Job):
      AZURE_STORAGE_ACCOUNT  — storage account name  (e.g. 'awardnomsa')
      MODEL_CONTAINER        — blob container name    (e.g. 'ml-models')
      AZURE_CLIENT_ID        — MI client ID for DefaultAzureCredential
      AZURE_STORAGE_KEY      — (optional) key auth for local dev
    """
    account   = os.getenv("AZURE_STORAGE_ACCOUNT")
    container = os.getenv("MODEL_CONTAINER", "ml-models")

    if not account:
        print(f"  ⚠  AZURE_STORAGE_ACCOUNT not set — skipping upload of {local_path.name}")
        return

    try:
        from azure.storage.blob import BlobServiceClient
        from azure.identity import DefaultAzureCredential

        storage_key = os.getenv("AZURE_STORAGE_KEY")
        if storage_key:
            # Local dev: key auth
            client = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=storage_key,
            )
        else:
            # Container: Managed Identity (AZURE_CLIENT_ID picked up automatically)
            client = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
            )

        blob_client = client.get_blob_client(container=container, blob=local_path.name)
        with open(local_path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)

        print(f"  ✓ Uploaded '{local_path.name}' → blob://{account}/{container}/{local_path.name}")

    except Exception as exc:
        # Non-fatal: model is still saved locally for the duration of the run.
        # The backend will continue to use the previous version from Blob Storage.
        print(f"  ✗ Blob upload failed for '{local_path.name}': {exc}")

# Minimum labelled samples needed to train a meaningful model.
# Below this threshold the tenant is skipped with a warning.
MIN_TRAINING_SAMPLES = 50

# All generated artefacts (.pkl models, .png charts) are written here.
OUTPUT_DIR = Path(__file__).resolve().parent / "Output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# DATABASE CONNECTION
# ============================================================================

def get_db_connection():
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
# TENANT DISCOVERY
# ============================================================================

def get_tenants(conn) -> list:
    """Return [(TenantId, TenantName), ...] for all tenants in the database."""
    df = pd.read_sql(
        "SELECT TenantId, TenantName FROM dbo.Tenants ORDER BY TenantId", conn
    )
    return list(df.itertuples(index=False, name=None))


# ============================================================================
# DATA LOADING  (per-tenant)
# ============================================================================

def load_data(tenant_id: int) -> pd.DataFrame:
    """
    Load all Paid nominations for a single tenant together with their
    fraud scores (if any have been labelled).

    Tenant isolation is achieved by joining through Users, which carries
    the TenantId foreign key.
    """
    print(f"\n[Tenant {tenant_id}] Loading data from database ...")

    conn = get_db_connection()

    query = """
    SELECT
        n.NominationId,
        n.NominatorId,
        n.BeneficiaryId,
        n.ApproverId,
        n.Amount,
        n.Currency,
        n.NominationDate,
        n.ApprovedDate,
        n.PayedDate,
        n.Status,
        fs.FraudScore,
        fs.RiskLevel,
        fs.FraudFlags,
        CASE
            WHEN fs.RiskLevel IN ('HIGH', 'CRITICAL') THEN 1
            ELSE 0
        END AS IsFraud
    FROM dbo.Nominations n
    JOIN dbo.Users u ON u.UserId = n.NominatorId
    LEFT JOIN dbo.FraudScores fs ON n.NominationId = fs.NominationId
    WHERE n.Status = 'Paid'
      AND u.TenantId = ?
    ORDER BY n.NominationDate
    """

    df = pd.read_sql(query, conn, params=[tenant_id])
    conn.close()

    print(f"[Tenant {tenant_id}] Loaded {len(df)} nominations")
    if len(df) > 0:
        fraud_count = df['IsFraud'].sum()
        print(
            f"[Tenant {tenant_id}] Fraud cases: {fraud_count} "
            f"({fraud_count / len(df) * 100:.2f}%)"
        )

    return df


# ============================================================================
# FEATURE ENGINEERING
# ============================================================================

def extract_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build all features used by the Random Forest.

    Key note on AmountZScore:
        Computed from the tenant-isolated dataset, so it reflects the
        per-tenant amount distribution (e.g. KRW 50 000–300 000 vs
        USD 50–300).  Do NOT compute z-scores across tenants.
    """
    print("  Extracting features ...")

    # ── Date parsing ────────────────────────────────────────────────────────
    df['NominationDate'] = pd.to_datetime(df['NominationDate'])
    df['ApprovedDate']   = pd.to_datetime(df['ApprovedDate'])
    df['PayedDate']      = pd.to_datetime(df['PayedDate'])

    # ── Temporal features ───────────────────────────────────────────────────
    df['DayOfWeek'] = df['NominationDate'].dt.dayofweek
    df['Month']     = df['NominationDate'].dt.month
    df['Hour']      = df['NominationDate'].dt.hour
    df['IsWeekend'] = df['DayOfWeek'].isin([5, 6]).astype(int)

    df['HoursToApproval'] = (
        (df['ApprovedDate'] - df['NominationDate']).dt.total_seconds() / 3600
    )
    df['HoursToPayment'] = (
        (df['PayedDate'] - df['ApprovedDate']).dt.total_seconds() / 3600
    )
    df['HoursToApproval'] = df['HoursToApproval'].replace([np.inf, -np.inf], np.nan)
    df['HoursToPayment']  = df['HoursToPayment'].replace([np.inf, -np.inf], np.nan)

    # ── Nominator behaviour ─────────────────────────────────────────────────
    print("  Calculating user behaviour features ...")

    nominator_stats = df.groupby('NominatorId').agg(
        NominatorTotalNominations=('NominationId', 'count'),
        NominatorAvgAmount=('Amount', 'mean'),
        NominatorStdAmount=('Amount', 'std'),
        NominatorMinAmount=('Amount', 'min'),
        NominatorMaxAmount=('Amount', 'max'),
        NominatorUniqueBeneficiaries=('BeneficiaryId', 'nunique'),
    ).reset_index()

    df = df.merge(nominator_stats, on='NominatorId', how='left')

    # ── Beneficiary behaviour ────────────────────────────────────────────────
    beneficiary_stats = df.groupby('BeneficiaryId').agg(
        BeneficiaryTotalReceived=('NominationId', 'count'),
        BeneficiaryAvgAmountReceived=('Amount', 'mean'),
    ).reset_index()

    df = df.merge(beneficiary_stats, on='BeneficiaryId', how='left')

    # ── Approver behaviour ───────────────────────────────────────────────────
    approver_stats = df.groupby('ApproverId').agg(
        ApproverTotalApproved=('NominationId', 'count'),
        ApproverAvgApprovalTime=('HoursToApproval', 'mean'),
    ).reset_index()

    df = df.merge(approver_stats, on='ApproverId', how='left')

    # ── Relationship features ────────────────────────────────────────────────
    print("  Calculating relationship features ...")

    reciprocal = df.merge(
        df[['NominatorId', 'BeneficiaryId']],
        left_on=['NominatorId', 'BeneficiaryId'],
        right_on=['BeneficiaryId', 'NominatorId'],
        how='inner',
        suffixes=('', '_reciprocal'),
    )
    df['HasReciprocalNomination'] = (
        df['NominationId'].isin(reciprocal['NominationId']).astype(int)
    )

    pair_counts = (
        df.groupby(['NominatorId', 'BeneficiaryId'])
          .size()
          .reset_index(name='PairNominationCount')
    )
    df = df.merge(pair_counts, on=['NominatorId', 'BeneficiaryId'], how='left')

    # ── Amount features (tenant-scoped z-score) ──────────────────────────────
    amount_mean = df['Amount'].mean()
    amount_std  = df['Amount'].std()

    df['AmountZScore'] = (
        (df['Amount'] - amount_mean) / amount_std
        if amount_std and amount_std > 0
        else 0.0
    )
    df['IsHighAmount'] = (df['AmountZScore'] > 2).astype(int)
    df['IsLowAmount']  = (df['AmountZScore'] < -2).astype(int)

    # ── Derived ratios ───────────────────────────────────────────────────────
    df['IsRapidApproval'] = (df['HoursToApproval'] < 1).astype(int)
    df['NominatorConcentrationRatio'] = (
        df['NominatorTotalNominations'] / (df['NominatorUniqueBeneficiaries'] + 1)
    )

    print("  Feature extraction complete.")
    return df


# ============================================================================
# FRAUD LABEL BOOTSTRAPPING
# ============================================================================

def bootstrap_fraud_labels(df: pd.DataFrame, tenant_id: int) -> pd.DataFrame:
    """
    Derive fraud labels from behavioural patterns when no FraudScores rows
    exist yet (the typical cold-start situation after the first load test).

    This is the chicken-and-egg problem: the model needs scored nominations
    to learn from, but scores only exist once a model is running.  Bootstrapping
    breaks the deadlock by using the patterns that the load generator deliberately
    embeds in the data:

      Fraudulent (10% of load): 8-12 rapid nominations, same nominator → same
          beneficiary, very short descriptions.  Signature: PairNominationCount > 7.

      Suspicious (20% of load): 3-5 nominations to a small pool.  Signature:
          PairNominationCount in [3, 7] with high concentration ratio.

      Normal (70% of load): single well-described nominations.

    Labels assigned:
      IsFraud = 1  →  PairNominationCount > 7   (clear fraudulent burst)
      IsFraud = 1  →  NominatorConcentrationRatio > 8 AND
                      NominatorTotalNominations  > 20  (concentrated + high volume)
      IsFraud = 0  →  everything else
    """
    df = df.copy()
    df['IsFraud'] = 0

    # Primary signal: repeated same-pair nominations (fraudulent burst pattern)
    df.loc[df['PairNominationCount'] > 7, 'IsFraud'] = 1

    # Secondary signal: highly concentrated nominator (few beneficiaries, many noms)
    df.loc[
        (df['NominatorConcentrationRatio'] > 8) &
        (df['NominatorTotalNominations']   > 20),
        'IsFraud'
    ] = 1

    fraud_n   = int(df['IsFraud'].sum())
    legit_n   = int((df['IsFraud'] == 0).sum())
    fraud_pct = fraud_n / len(df) * 100

    print(
        f"[Tenant {tenant_id}] ⚡ Bootstrapped labels: "
        f"{fraud_n} fraud ({fraud_pct:.1f}%), {legit_n} legitimate"
    )

    if fraud_n == 0:
        raise ValueError(
            f"[Tenant {tenant_id}] Bootstrap found no fraud patterns in the data. "
            "Make sure the load generator ran with suspicious/fraudulent scenarios "
            "(default 30% of traffic) before training."
        )

    return df


# ============================================================================
# MODEL TRAINING  (per-tenant)
# ============================================================================

FEATURE_COLUMNS = [
    'Amount',
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
    'NominatorConcentrationRatio',
]


def _risk_level(score: int) -> str:
    if score >= 80: return 'CRITICAL'
    if score >= 60: return 'HIGH'
    if score >= 40: return 'MEDIUM'
    if score >= 20: return 'LOW'
    return 'NONE'


def score_and_save_historical(
    df: pd.DataFrame,
    model_data: dict,
    tenant_id: int,
) -> None:
    """
    Score every nomination in df with the freshly trained model and upsert
    the results into dbo.FraudScores.

    Why this matters:
      - Populates the FraudScores table so the analytics Fraud Score
        Distribution chart has real data to display immediately after training.
      - On the *next* retrain, load_data() will find real RiskLevel labels
        (HIGH / CRITICAL) in FraudScores and use them instead of bootstrapped
        rules, progressively improving model quality with every cycle.

    Uses SQL Server MERGE so the function is safe to call multiple times —
    existing rows are updated with scores from the improved model, missing
    rows are inserted.
    """
    print(f"\n[Tenant {tenant_id}] Scoring {len(df)} historical nominations ...")

    rf_model        = model_data['model']
    scaler          = model_data['scaler']
    feature_columns = model_data['feature_columns']

    X        = df[feature_columns].fillna(0)
    X_scaled = scaler.transform(X)
    probas   = rf_model.predict_proba(X_scaled)

    if probas.shape[1] < 2:
        print(f"[Tenant {tenant_id}] ⚠  Single-class model — skipping score persistence.")
        return

    fraud_probs = probas[:, 1]

    conn   = get_db_connection()
    cursor = conn.cursor()

    upserted = 0
    for i, (_, row) in enumerate(df.iterrows()):
        nom_id = int(row['NominationId'])
        prob   = float(fraud_probs[i])
        score  = int(prob * 100)
        level  = _risk_level(score)

        flags = []
        if row.get('PairNominationCount', 0) > 5:
            flags.append('Repeated beneficiary')
        if row.get('HasReciprocalNomination', 0) == 1:
            flags.append('Reciprocal nomination detected')
        if row.get('NominatorConcentrationRatio', 0) > 5:
            flags.append('Limited beneficiary diversity')
        if row.get('IsHighAmount', 0) == 1:
            flags.append('Unusually high amount')
        flags_str = ', '.join(flags)

        cursor.execute(
            """
            MERGE dbo.FraudScores AS target
            USING (SELECT ? AS NominationId) AS source
                ON target.NominationId = source.NominationId
            WHEN MATCHED THEN
                UPDATE SET FraudScore = ?, RiskLevel = ?, FraudFlags = ?
            WHEN NOT MATCHED THEN
                INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
                VALUES (?,            ?,          ?,         ?);
            """,
            (nom_id, score, level, flags_str, nom_id, score, level, flags_str),
        )
        upserted += 1

    conn.commit()
    cursor.close()
    conn.close()

    high_risk = sum(
        1 for p in fraud_probs if int(p * 100) >= 60
    )
    print(
        f"[Tenant {tenant_id}] ✓ Upserted {upserted} fraud scores "
        f"({high_risk} HIGH/CRITICAL)"
    )


def train_model(df: pd.DataFrame, tenant_id: int) -> tuple[dict, dict]:
    """
    Train a Random Forest for one tenant and persist it to
    Output/fraud_detection_model_tenant_{tenant_id}.pkl.
    """
    print(f"\n[Tenant {tenant_id}] Training model ...")

    df = extract_features(df)

    # If no FraudScores have been recorded yet (cold start after first load test),
    # derive labels from the behavioural patterns embedded in the data.
    if df['IsFraud'].sum() == 0:
        print(
            f"[Tenant {tenant_id}] ⚠  No FraudScores labels found — "
            "bootstrapping from behavioural patterns."
        )
        df = bootstrap_fraud_labels(df, tenant_id)

    df_train = df[df['IsFraud'].notna()].copy()
    if len(df_train) < MIN_TRAINING_SAMPLES:
        raise ValueError(
            f"[Tenant {tenant_id}] Only {len(df_train)} labelled samples — "
            f"need at least {MIN_TRAINING_SAMPLES} to train.  "
            f"Run the load generator and label more data first."
        )

    n_fraud = int(df_train['IsFraud'].sum())
    n_legit = int((df_train['IsFraud'] == 0).sum())
    if n_fraud == 0 or n_legit == 0:
        raise ValueError(
            f"[Tenant {tenant_id}] Training set has only one class "
            f"(fraud={n_fraud}, legitimate={n_legit}). "
            "A classifier requires both classes. "
            "Run more load test traffic or check bootstrap thresholds."
        )

    print(
        f"[Tenant {tenant_id}]   Class balance — "
        f"legitimate: {n_legit}, fraud: {n_fraud} "
        f"({n_fraud / len(df_train) * 100:.1f}%)"
    )

    X = df_train[FEATURE_COLUMNS].fillna(0)
    y = df_train['IsFraud']

    print(f"[Tenant {tenant_id}]   Training data shape: {X.shape}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled  = scaler.transform(X_test)

    rf_model = RandomForestClassifier(
        n_estimators=40,      # 100 → 40: ~60% memory saving with negligible AUC loss
        max_depth=10,         # already capped — keeps each tree compact
        min_samples_split=20,
        min_samples_leaf=10,
        class_weight='balanced',
        random_state=42,
        n_jobs=-1,
    )
    rf_model.fit(X_train_scaled, y_train)

    # ── Evaluation ───────────────────────────────────────────────────────────
    y_pred       = rf_model.predict(X_test_scaled)
    y_pred_proba = rf_model.predict_proba(X_test_scaled)[:, 1]

    print(f"\n{'='*60}")
    print(f"MODEL EVALUATION — Tenant {tenant_id}")
    print(f"{'='*60}")
    print(classification_report(y_test, y_pred, target_names=['Legitimate', 'Fraud']))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, y_pred))

    auc = None
    if len(np.unique(y_test)) > 1:
        auc = roc_auc_score(y_test, y_pred_proba)
        print(f"ROC AUC Score: {auc:.4f}")

    feature_importance = pd.DataFrame({
        'Feature': FEATURE_COLUMNS,
        'Importance': rf_model.feature_importances_,
    }).sort_values('Importance', ascending=False)

    print("\nTop 10 Most Important Features:")
    print(feature_importance.head(10).to_string(index=False))

    # ── Persist ──────────────────────────────────────────────────────────────
    # Only inference-critical fields go into the pkl.  Diagnostic fields
    # (feature_importance, auc, training_date, training_samples) are logged
    # above but not stored — they are never read by fraud_ml.py at inference
    # time and were the second-largest contributor to pkl size after the RF
    # tree structures themselves.
    model_data = {
        'model':           rf_model,
        'scaler':          scaler,
        'feature_columns': FEATURE_COLUMNS,
        # Tenant-scoped amount stats — used by fraud_ml.py to compute
        # z-scores at inference time without crossing tenant boundaries.
        'amount_mean':     float(df['Amount'].mean()),
        'amount_std':      float(df['Amount'].std()),
    }

    pkl_filename = OUTPUT_DIR / f"fraud_detection_model_tenant_{tenant_id}.pkl"
    with open(pkl_filename, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\n✓ Model saved to '{pkl_filename}'")
    _upload_artefact(pkl_filename)

    # ── Score all historical nominations and persist to dbo.FraudScores ──────
    # This is the step that was missing: without it FraudScores stays empty,
    # the analytics charts have no data, and every retrain is forced back to
    # bootstrapped labels instead of graduating to real scored labels.
    score_and_save_historical(df, model_data, tenant_id)

    # ── Visualisations ───────────────────────────────────────────────────────
    # Re-fetch df so the Fraud Score Distribution chart reflects the scores
    # just written to dbo.FraudScores.
    df_with_scores = load_data(tenant_id)
    create_visualizations(df_with_scores, feature_importance, y_test, y_pred_proba, tenant_id)

    # Return model_data (for callers) plus lightweight diagnostics separately
    # so main() can log them without reading back from the stripped pkl dict.
    return model_data, {'auc': auc, 'training_samples': len(df_train)}


# ============================================================================
# VISUALISATIONS
# ============================================================================

def create_visualizations(
    df: pd.DataFrame,
    feature_importance: pd.DataFrame,
    y_test,
    y_pred_proba,
    tenant_id: int,
) -> None:
    print(f"\n[Tenant {tenant_id}] Creating visualisations ...")

    fig, axes = plt.subplots(2, 2, figsize=(15, 12))
    fig.suptitle(f"Fraud Detection Analysis — Tenant {tenant_id}", fontsize=14)

    # 1. Feature importance
    axes[0, 0].barh(
        feature_importance.head(10)['Feature'],
        feature_importance.head(10)['Importance'],
    )
    axes[0, 0].set_xlabel('Importance')
    axes[0, 0].set_title('Top 10 Feature Importances')
    axes[0, 0].invert_yaxis()

    # 2. Fraud-score distribution
    fraud_df = df[df['IsFraud'].notna()]
    if 'FraudScore' in fraud_df.columns and fraud_df['FraudScore'].notna().any():
        axes[0, 1].hist(
            [
                fraud_df[fraud_df['IsFraud'] == 0]['FraudScore'].dropna(),
                fraud_df[fraud_df['IsFraud'] == 1]['FraudScore'].dropna(),
            ],
            bins=30, label=['Legitimate', 'Fraud'], alpha=0.7,
        )
    axes[0, 1].set_xlabel('Fraud Score')
    axes[0, 1].set_ylabel('Count')
    axes[0, 1].set_title('Fraud Score Distribution')
    axes[0, 1].legend()

    # 3. ROC curve
    if len(np.unique(y_test)) > 1:
        fpr, tpr, _ = roc_curve(y_test, y_pred_proba)
        auc_val = roc_auc_score(y_test, y_pred_proba)
        axes[1, 0].plot(fpr, tpr, label=f'ROC Curve (AUC = {auc_val:.3f})')
        axes[1, 0].plot([0, 1], [0, 1], 'k--', label='Random')
        axes[1, 0].set_xlabel('False Positive Rate')
        axes[1, 0].set_ylabel('True Positive Rate')
        axes[1, 0].set_title('ROC Curve')
        axes[1, 0].legend()

    # 4. Nominations by risk level
    if 'RiskLevel' in df.columns and df['RiskLevel'].notna().any():
        risk_counts = df[df['RiskLevel'].notna()]['RiskLevel'].value_counts()
        axes[1, 1].bar(risk_counts.index, risk_counts.values)
        axes[1, 1].set_xlabel('Risk Level')
        axes[1, 1].set_ylabel('Count')
        axes[1, 1].set_title('Nominations by Risk Level')
        axes[1, 1].tick_params(axis='x', rotation=45)

    plt.tight_layout()
    png_filename = OUTPUT_DIR / f"fraud_detection_analysis_tenant_{tenant_id}.png"
    plt.savefig(png_filename, dpi=300, bbox_inches='tight')
    print(f"✓ Visualisation saved to '{png_filename}'")
    _upload_artefact(png_filename)
    plt.close()


# ============================================================================
# MAIN — iterate over all tenants
# ============================================================================

def main() -> None:
    """Entry point called by run_job.py (Stage 1)."""
    print("=" * 60)
    print("FRAUD DETECTION MODEL TRAINING  —  Multi-Tenant")
    print("=" * 60)

    conn = get_db_connection()
    tenants = get_tenants(conn)
    conn.close()

    print(f"\nFound {len(tenants)} tenant(s): {[t[0] for t in tenants]}")

    results = {}
    failed = []
    for tenant_id, tenant_name in tenants:
        print(f"\n{'='*60}")
        print(f"  Tenant {tenant_id}: {tenant_name}")
        print(f"{'='*60}")

        try:
            df = load_data(tenant_id)

            if len(df) < MIN_TRAINING_SAMPLES:
                print(
                    f"⚠  Skipping Tenant {tenant_id} — only {len(df)} samples "
                    f"(minimum {MIN_TRAINING_SAMPLES} required)."
                )
                results[tenant_id] = "SKIPPED (insufficient data)"
                continue

            _, stats = train_model(df, tenant_id)
            results[tenant_id] = (
                f"OK  ({stats['training_samples']} samples, "
                f"AUC={stats['auc']:.4f})"
                if stats['auc'] else
                f"OK  ({stats['training_samples']} samples)"
            )

        except Exception as exc:
            print(f"❌  Tenant {tenant_id} failed: {exc}")
            results[tenant_id] = f"FAILED — {exc}"
            failed.append(tenant_id)

    print("\n" + "=" * 60)
    print("TRAINING SUMMARY")
    print("=" * 60)
    for tenant_id, status in results.items():
        print(f"  Tenant {tenant_id}: {status}")

    if failed:
        raise RuntimeError(
            f"RF training failed for tenant(s): {failed} — see output above."
        )


if __name__ == "__main__":
    main()
