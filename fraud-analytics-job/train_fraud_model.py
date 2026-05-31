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

After training, each .pkl is uploaded directly to Azure Blob Storage under
the same filename.  The backend FraudDetector streams models directly from
blob (no local copy); idle models are evicted from the in-process cache after
MODEL_IDLE_TTL_SECONDS.  The next request after eviction re-streams the blob,
so fresh models propagate automatically within one TTL period of upload — no
container restart required.
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
        f"Connection Timeout=60;"
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
        n.CategoryId,
        p2p.FraudScore,
        p2p.RiskLevel,
        p2p.FraudFlags,
        CASE
            WHEN p2p.RiskLevel IN ('HIGH', 'CRITICAL') THEN 1
            ELSE 0
        END AS IsFraud
    FROM dbo.Nominations n
    JOIN dbo.Users u ON u.UserId = n.NominatorId
    LEFT JOIN dbo.P2P_FraudScores p2p ON p2p.NominationId = n.NominationId
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

    # ── Nomination category — target encoding ────────────────────────────────
    # Replace CategoryId with a single float: the mean fraud rate for that
    # category in the training set.  This is stable across category changes:
    # adding or removing a category never changes the feature space, and
    # unknown categories at inference time get a neutral fallback (global mean).
    # Nominations with no category (NULL) get 0.0 — a distinct neutral signal.
    if 'CategoryId' in df.columns and 'IsFraud' in df.columns:
        global_fraud_rate = df['IsFraud'].mean()
        category_fraud_rate = (
            df.groupby('CategoryId')['IsFraud']
              .mean()
              .to_dict()
        )
        df['CategoryFraudRate'] = df['CategoryId'].map(category_fraud_rate).fillna(0.0)
    else:
        df['CategoryFraudRate'] = 0.0
        category_fraud_rate    = {}
        global_fraud_rate      = 0.0

    print("  Feature extraction complete.")
    return df, category_fraud_rate, global_fraud_rate


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

# ── P2P feature columns ───────────────────────────────────────────────────────
# All knowable at nomination submission time (peer-to-peer signals).
# Used for live fraud scoring in the backend.
P2P_FEATURE_COLUMNS = [
    'Amount',
    'DayOfWeek',
    'Month',
    'IsWeekend',
    'NominatorTotalNominations',
    'NominatorAvgAmount',
    'NominatorStdAmount',
    'NominatorUniqueBeneficiaries',
    'BeneficiaryTotalReceived',
    'BeneficiaryAvgAmountReceived',
    'HasReciprocalNomination',
    'PairNominationCount',
    'AmountZScore',
    'IsHighAmount',
    'NominatorConcentrationRatio',
    'CategoryFraudRate',
]

# ── Approver feature columns ──────────────────────────────────────────────────
# Post-decision measurements — only available after a nomination is Paid.
# Used by the batch job to detect approver-side fraud patterns.
APPR_FEATURE_COLUMNS = [
    'ApproverTotalApproved',
    'ApproverAvgApprovalTime',
    'HoursToApproval',
    'HoursToPayment',
    'IsRapidApproval',
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
    Score every nomination in df with both the P2P and Approver models
    and upsert results into dbo.P2P_FraudScores and dbo.Appr_FraudScores.

    - P2P scores feed the analytics fraud dashboard and provide labels for
      the next retrain cycle (progressive label improvement).
    - Approver scores capture post-decision fraud patterns independently.
    - Approver labels are derived from P2P: a nomination is approver-fraud
      if the P2P model scored it HIGH/CRITICAL AND IsRapidApproval = 1.
    """
    print(f"\n[Tenant {tenant_id}] Scoring {len(df)} historical nominations ...")

    conn   = get_db_connection()
    cursor = conn.cursor()

    # ── P2P scoring ──────────────────────────────────────────────────────────
    p2p_rf     = model_data['p2p_model']
    p2p_scaler = model_data['p2p_scaler']
    p2p_cols   = model_data['p2p_feature_columns']

    p2p_probas = p2p_rf.predict_proba(p2p_scaler.transform(df[p2p_cols].fillna(0)))
    if p2p_probas.shape[1] < 2:
        print(f"[Tenant {tenant_id}] ⚠  P2P single-class model — skipping P2P score persistence.")
        p2p_fraud_probs = None
    else:
        p2p_fraud_probs = p2p_probas[:, 1]

    # ── Approver scoring ─────────────────────────────────────────────────────
    appr_rf     = model_data['appr_model']
    appr_scaler = model_data['appr_scaler']
    appr_cols   = model_data['appr_feature_columns']

    appr_probas = appr_rf.predict_proba(appr_scaler.transform(df[appr_cols].fillna(0)))
    if appr_probas.shape[1] < 2:
        print(f"[Tenant {tenant_id}] ⚠  Approver single-class model — skipping approver score persistence.")
        appr_fraud_probs = None
    else:
        appr_fraud_probs = appr_probas[:, 1]

    p2p_upserted = appr_upserted = 0

    for i, (_, row) in enumerate(df.iterrows()):
        nom_id = int(row['NominationId'])

        # P2P
        if p2p_fraud_probs is not None:
            p2p_prob  = float(p2p_fraud_probs[i])
            p2p_score = int(p2p_prob * 100)
            p2p_level = _risk_level(p2p_score)
            p2p_flags = []
            if row.get('PairNominationCount', 0) > 5:
                p2p_flags.append('Repeated beneficiary')
            if row.get('HasReciprocalNomination', 0) == 1:
                p2p_flags.append('Reciprocal nomination detected')
            if row.get('NominatorConcentrationRatio', 0) > 5:
                p2p_flags.append('Limited beneficiary diversity')
            if row.get('IsHighAmount', 0) == 1:
                p2p_flags.append('Unusually high amount')
            cursor.execute(
                """
                MERGE dbo.P2P_FraudScores AS target
                USING (SELECT ? AS NominationId) AS source
                    ON target.NominationId = source.NominationId
                WHEN MATCHED THEN
                    UPDATE SET FraudScore = ?, RiskLevel = ?, FraudFlags = ?
                WHEN NOT MATCHED THEN
                    INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
                    VALUES (?,            ?,          ?,         ?);
                """,
                (nom_id, p2p_score, p2p_level, ', '.join(p2p_flags),
                 nom_id, p2p_score, p2p_level, ', '.join(p2p_flags)),
            )
            p2p_upserted += 1

        # Approver
        if appr_fraud_probs is not None:
            appr_prob  = float(appr_fraud_probs[i])
            appr_score = int(appr_prob * 100)
            appr_level = _risk_level(appr_score)
            appr_flags = []
            if row.get('IsRapidApproval', 0) == 1:
                appr_flags.append('Rapid approval')
            if row.get('HoursToPayment', 0) < 24 and row.get('HoursToPayment', 0) > 0:
                appr_flags.append('Fast payment')
            cursor.execute(
                """
                MERGE dbo.Appr_FraudScores AS target
                USING (SELECT ? AS NominationId) AS source
                    ON target.NominationId = source.NominationId
                WHEN MATCHED THEN
                    UPDATE SET FraudScore = ?, RiskLevel = ?, FraudFlags = ?
                WHEN NOT MATCHED THEN
                    INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
                    VALUES (?,            ?,          ?,         ?);
                """,
                (nom_id, appr_score, appr_level, ', '.join(appr_flags),
                 nom_id, appr_score, appr_level, ', '.join(appr_flags)),
            )
            appr_upserted += 1

    conn.commit()
    cursor.close()
    conn.close()

    p2p_high = sum(1 for p in (p2p_fraud_probs or []) if int(p * 100) >= 60)
    print(
        f"[Tenant {tenant_id}] ✓ P2P: {p2p_upserted} upserted ({p2p_high} HIGH/CRITICAL) | "
        f"Approver: {appr_upserted} upserted"
    )


def train_model(df: pd.DataFrame, tenant_id: int) -> tuple[dict, dict]:
    """
    Train a Random Forest for one tenant and persist it to
    Output/fraud_detection_model_tenant_{tenant_id}.pkl.
    """
    print(f"\n[Tenant {tenant_id}] Training model ...")

    df, category_fraud_rate, global_fraud_rate = extract_features(df)

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

    if category_fraud_rate:
        print(f"[Tenant {tenant_id}]   Category target encoding: {category_fraud_rate}")
    else:
        print(f"[Tenant {tenant_id}]   No nomination categories — CategoryFraudRate=0.0 for all rows.")

    y = df_train['IsFraud']

    def _train_rf(X: pd.DataFrame, label: str) -> tuple:
        """Train one RF, print evaluation, return (model, scaler, auc)."""
        print(f"\n[Tenant {tenant_id}] Training {label} model — shape: {X.shape}")
        X_tr, X_te, y_tr, y_te = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        scaler_ = StandardScaler()
        X_tr_s  = scaler_.fit_transform(X_tr)
        X_te_s  = scaler_.transform(X_te)

        rf_ = RandomForestClassifier(
            n_estimators=40,
            max_depth=10,
            min_samples_split=20,
            min_samples_leaf=10,
            class_weight='balanced',
            random_state=42,
            n_jobs=-1,
        )
        rf_.fit(X_tr_s, y_tr)

        y_pred_  = rf_.predict(X_te_s)
        y_proba_ = rf_.predict_proba(X_te_s)[:, 1]

        print(f"\n{'='*60}")
        print(f"{label.upper()} MODEL EVALUATION — Tenant {tenant_id}")
        print(f"{'='*60}")
        print(classification_report(y_te, y_pred_, target_names=['Legitimate', 'Fraud']))
        print("Confusion Matrix:")
        print(confusion_matrix(y_te, y_pred_))

        auc_ = None
        if len(np.unique(y_te)) > 1:
            auc_ = roc_auc_score(y_te, y_proba_)
            print(f"ROC AUC Score: {auc_:.4f}")

        fi = pd.DataFrame({'Feature': X.columns.tolist(),
                           'Importance': rf_.feature_importances_}) \
               .sort_values('Importance', ascending=False)
        print(f"\nTop 10 Most Important {label} Features:")
        print(fi.head(10).to_string(index=False))

        return rf_, scaler_, auc_

    # ── Train P2P model ───────────────────────────────────────────────────────
    p2p_rf, p2p_scaler, p2p_auc = _train_rf(
        df_train[P2P_FEATURE_COLUMNS].fillna(0), "P2P"
    )

    # ── Derive approver fraud labels from P2P scores ──────────────────────────
    # An approver is suspicious when they fast-approve a nomination the P2P
    # model already considers HIGH/CRITICAL risk.
    p2p_probas_full = p2p_rf.predict_proba(
        p2p_scaler.transform(df_train[P2P_FEATURE_COLUMNS].fillna(0))
    )[:, 1]
    p2p_high_risk = (p2p_probas_full * 100 >= 60).astype(int)
    df_train = df_train.copy()
    df_train['IsApproverFraud'] = (
        (p2p_high_risk == 1) & (df_train['IsRapidApproval'] == 1)
    ).astype(int)

    appr_auc = None
    if df_train['IsApproverFraud'].sum() > 0:
        appr_rf, appr_scaler, appr_auc = _train_rf(
            df_train[APPR_FEATURE_COLUMNS].fillna(0), "Approver"
        )
    else:
        print(
            f"[Tenant {tenant_id}] ⚠  No approver-fraud labels derived — "
            "skipping approver model training. "
            "Need HIGH/CRITICAL P2P nominations with IsRapidApproval=1."
        )
        # Placeholder: reuse P2P model as fallback so pkl is always complete.
        appr_rf     = p2p_rf
        appr_scaler = p2p_scaler

    # ── Persist pkl ───────────────────────────────────────────────────────────
    model_data = {
        # P2P model — used for live submission-time fraud scoring
        'p2p_model':            p2p_rf,
        'p2p_scaler':           p2p_scaler,
        'p2p_feature_columns':  P2P_FEATURE_COLUMNS,
        # Approver model — used by the weekly batch job
        'appr_model':           appr_rf,
        'appr_scaler':          appr_scaler,
        'appr_feature_columns': APPR_FEATURE_COLUMNS,
        # Tenant-scoped amount stats for z-score computation at inference time
        'amount_mean':          float(df['Amount'].mean()),
        'amount_std':           float(df['Amount'].std()),
        # Category target encoding
        'category_fraud_rate':  category_fraud_rate,
        'global_fraud_rate':    float(global_fraud_rate),
    }

    pkl_filename = OUTPUT_DIR / f"fraud_detection_model_tenant_{tenant_id}.pkl"
    with open(pkl_filename, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\n✓ Model saved to '{pkl_filename}'")
    _upload_artefact(pkl_filename)

    # ── Score all historical nominations into both score tables ───────────────
    score_and_save_historical(df, model_data, tenant_id)

    # ── Visualisations ────────────────────────────────────────────────────────
    df_with_scores = load_data(tenant_id)
    create_visualizations(df_with_scores, None, None, None, tenant_id)

    return model_data, {
        'p2p_auc': p2p_auc, 'appr_auc': appr_auc,
        'training_samples': len(df_train)
    }


# ============================================================================
# VISUALISATIONS
# ============================================================================

def create_visualizations(df: pd.DataFrame, _fi, _yt, _yp, tenant_id: int) -> None:
    """
    Generate and upload a fraud score distribution chart.
    Feature importance and ROC curve are now printed to stdout during training
    rather than stored in the visualisation — they are diagnostic output, not
    dashboarding data.
    """
    print(f"\n[Tenant {tenant_id}] Creating visualisations ...")

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"Fraud Score Distribution — Tenant {tenant_id}", fontsize=13)

    for ax, (score_col, label, table) in zip(axes, [
        ('P2PFraudScore',  'P2P',      'P2P_FraudScores'),
        ('ApprFraudScore', 'Approver',  'Appr_FraudScores'),
    ]):
        if score_col in df.columns and df[score_col].notna().any():
            legit = df[df['IsFraud'] == 0][score_col].dropna()
            fraud = df[df['IsFraud'] == 1][score_col].dropna()
            ax.hist([legit, fraud], bins=30, label=['Legitimate', 'Fraud'], alpha=0.7)
        ax.set_xlabel('Fraud Score')
        ax.set_ylabel('Count')
        ax.set_title(f'{label} Score Distribution')
        ax.legend()

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
            p2p_auc_str  = f"{stats['p2p_auc']:.4f}"  if stats.get('p2p_auc')  else "n/a"
            appr_auc_str = f"{stats['appr_auc']:.4f}" if stats.get('appr_auc') else "n/a"
            results[tenant_id] = (
                f"OK  ({stats['training_samples']} samples, "
                f"P2P AUC={p2p_auc_str}, Approver AUC={appr_auc_str})"
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
