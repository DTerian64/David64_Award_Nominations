"""
Fraud Detection ML Model Training  —  Multi-Tenant Edition
===========================================================

Trains one Random Forest model per tenant and saves each to its own pickle:
    Output/random_forest_tenant_1.pkl
    Output/random_forest_tenant_2.pkl
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

import json
import os
import uuid
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import pyodbc
import pickle
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score, roc_curve
from sklearn.metrics.pairwise import cosine_similarity as sk_cosine_similarity
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from pathlib import Path
from sentence_transformers import SentenceTransformer

from component_status import upsert_component_status

# Default sentence-transformer model for English tenants.
# Per-tenant overrides are read from dbo.Tenants.desc_check_config at
# training time and stored in the pkl so inference/random_forest_check.py loads the same
# model at inference time (e.g. 'paraphrase-multilingual-MiniLM-L12-v2'
# for Korean / Japanese / other non-English tenants).
DEFAULT_EMBED_MODEL_NAME = 'all-MiniLM-L6-v2'

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

# ── Blob Storage upload helper ─────────────────────────────────────────────────

def _upload_artefact(local_path: Path) -> None:
    """
    Upload a local file to Azure Blob Storage under its local filename.
    Uses the User-Assigned Managed Identity
    injected via MI_CLIENT_ID; falls back to env-var key auth when
    running locally with AZURE_STORAGE_KEY set.

    Env vars (set by Terraform / Container Apps Job):
      AZURE_STORAGE_ACCOUNT  — storage account name  (e.g. 'awardnomsa')
      MODEL_CONTAINER        — blob container name    (e.g. 'ml-models')
      MI_CLIENT_ID        — MI client ID for DefaultAzureCredential
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
            # Container: Managed Identity (MI_CLIENT_ID picked up automatically)
            client = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=DefaultAzureCredential(managed_identity_client_id=os.getenv("MI_CLIENT_ID")),
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

from db_conn import connect


def get_db_connection():
    return connect()


# ============================================================================
# TENANT DISCOVERY
# ============================================================================

def get_tenants(conn) -> list:
    """Return [(TenantId, TenantName), ...] for all tenants in the database."""
    df = pd.read_sql(
        "SELECT TenantId, TenantName FROM dbo.Tenants ORDER BY TenantId", conn
    )
    return list(df.itertuples(index=False, name=None))


def get_tenant_embed_model(tenant_id: int) -> str:
    """
    Read the sentence-transformer model name from dbo.Tenants.desc_check_config.

    Falls back to DEFAULT_EMBED_MODEL_NAME when the column is NULL, the JSON
    is malformed, or the 'embed_model' key is absent.  This ensures the model
    stored in the pkl always matches what inference/random_forest_check.py will load at inference.
    """
    import json as _json
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT desc_check_config FROM dbo.Tenants WHERE TenantId = ?",
            (tenant_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if not row or not row[0]:
        return DEFAULT_EMBED_MODEL_NAME
    try:
        data = _json.loads(row[0])
        return data.get("embed_model", DEFAULT_EMBED_MODEL_NAME)
    except (_json.JSONDecodeError, TypeError):
        print(
            f"[Tenant {tenant_id}] ⚠  Could not parse desc_check_config JSON — "
            f"using default embed model '{DEFAULT_EMBED_MODEL_NAME}'."
        )
        return DEFAULT_EMBED_MODEL_NAME


# ============================================================================
# DATA LOADING  (per-tenant)
# ============================================================================

def load_data(tenant_id: int) -> pd.DataFrame:
    """
    Load nominations for a single tenant together with their fraud scores.

    Inclusion rules:
      • PendingHRBPReview — excluded (no confirmed label yet; don't train on ambiguous cases)
      • Rejected by 'Fraud Detection (Description)' (auto-reject, Check A) — excluded
        (description quality gate, not a fraud signal; would corrupt IsFraud labels)
      • Rejected by 'Fraud Detection' (CRITICAL ML auto-reject) — INCLUDED. Before
        2026-08 both rejection paths wrote the same actor string, so this exclusion
        also discarded every ML auto-reject. Tenant 1 has zero such rows today, so
        splitting the actor is behaviour-preserving on current data — but note that
        once these rows appear they carry LabelSource='model', i.e. the Random Forest
        training on its own unreviewed output.
      • Rejected by 'HRBP Review' — INCLUDED; these have a confirmed IsFraud=1 label written
        by upsert_p2p_fraud_label and are the most valuable training examples
      • All other statuses (Pending, Approved, Paid, Submitted) — included

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
        n.NominationDescription,
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
        END AS IsFraud,

        -- ── Graph features: nominator snapshot (point-in-time) ──────────────
        -- OUTER APPLY picks the closest UserGraphFlags snapshot whose AsOfDate
        -- is ≤ the nomination date.  If no prior snapshot exists, all columns
        -- are NULL and fillna(0) in the RF pipeline treats them as no-signal.
        ISNULL(ugf_n.IsInRing,                 0) AS NominatorIsInRing,
        ISNULL(ugf_n.IsSuperNominator,         0) AS SuperNominatorFlag,
        ISNULL(ugf_n.IsInCopyPasteCluster,     0) AS NominatorInCopyPaste,
        ISNULL(ugf_n.CopyPasteClusterSize,     0) AS NominatorClusterSize,
        ISNULL(ugf_n.HasTransactionalLanguage, 0) AS NominatorTransactional,

        -- ── Graph features: beneficiary snapshot (point-in-time) ────────────
        ISNULL(ugf_b.IsInRing,                 0) AS BeneficiaryIsInRing,
        ISNULL(ugf_b.IsInCopyPasteCluster,     0) AS BeneficiaryInCopyPaste,
        ISNULL(ugf_b.CopyPasteClusterSize,     0) AS BeneficiaryClusterSize,
        ISNULL(ugf_b.HasTransactionalLanguage, 0) AS BeneficiaryTransactional,

        -- ── Graph features: approver snapshot (point-in-time) ───────────────
        ISNULL(ugf_a.IsApproverAffinity,       0) AS ApproverAffinityFlag,

        -- ── ApproverPairFlags: how many times has this approver approved
        --    this exact nominator→beneficiary pair? (point-in-time) ──────────
        ISNULL(apf.PairApprovalCount,          0) AS GraphApproverPairCount

    FROM dbo.Nominations n
    JOIN dbo.Users u ON u.UserId = n.NominatorId
    LEFT JOIN dbo.P2P_FraudScores p2p ON p2p.NominationId = n.NominationId

    -- Nominator graph flags as of nomination date
    OUTER APPLY (
        SELECT TOP 1
               IsInRing, IsSuperNominator,
               IsInCopyPasteCluster, CopyPasteClusterSize,
               HasTransactionalLanguage
        FROM   dbo.UserGraphFlags
        WHERE  TenantId = u.TenantId
          AND  UserId   = n.NominatorId
          AND  AsOfDate <= CAST(n.NominationDate AS DATE)
        ORDER  BY AsOfDate DESC
    ) ugf_n

    -- Beneficiary graph flags as of nomination date
    OUTER APPLY (
        SELECT TOP 1
               IsInRing,
               IsInCopyPasteCluster, CopyPasteClusterSize,
               HasTransactionalLanguage
        FROM   dbo.UserGraphFlags
        WHERE  TenantId = u.TenantId
          AND  UserId   = n.BeneficiaryId
          AND  AsOfDate <= CAST(n.NominationDate AS DATE)
        ORDER  BY AsOfDate DESC
    ) ugf_b

    -- Approver graph flags as of nomination date
    OUTER APPLY (
        SELECT TOP 1 IsApproverAffinity
        FROM   dbo.UserGraphFlags
        WHERE  TenantId = u.TenantId
          AND  UserId   = n.ApproverId
          AND  AsOfDate <= CAST(n.NominationDate AS DATE)
        ORDER  BY AsOfDate DESC
    ) ugf_a

    -- Approver-pair count as of nomination date
    OUTER APPLY (
        SELECT TOP 1 PairApprovalCount
        FROM   dbo.ApproverPairFlags
        WHERE  TenantId      = u.TenantId
          AND  ApproverId    = n.ApproverId
          AND  NominatorId   = n.NominatorId
          AND  BeneficiaryId = n.BeneficiaryId
          AND  AsOfDate      <= CAST(n.NominationDate AS DATE)
        ORDER  BY AsOfDate DESC
    ) apf

    WHERE n.Status NOT IN ('PendingHRBPReview')
      AND NOT (n.Status = 'Rejected' AND n.RejectionActor = 'Fraud Detection (Description)')
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
# SEMANTIC FEATURE ENGINEERING
# ============================================================================

def add_semantic_features(df: pd.DataFrame, embed_model: SentenceTransformer) -> pd.DataFrame:
    """
    Add two semantic features derived from NominationDescription:

      DescriptionCosineSim   — cosine similarity between the nominator's
          current description and the mean embedding of all descriptions
          the beneficiary has ever written as a nominator.  High similarity
          suggests coordinated / copy-pasted text between ring members.

      DescriptionEmbDistance — Euclidean distance in the same embedding
          space.  Low distance (< 0.3) combined with high cosine sim is a
          strong collusion signal.

    Both features fall back to neutral values (0.0 / 1.0 respectively) when
    either party has no past descriptions to compare against.

    The embed_model is passed in so the caller controls when it is loaded
    (once per training run, not once per tenant).
    """
    print("  Computing semantic description features ...")

    descriptions = df['NominationDescription'].fillna('').tolist()
    if not any(descriptions):
        print("  ⚠  All NominationDescriptions are empty — semantic features set to neutral.")
        df['DescriptionCosineSim']   = 0.0
        df['DescriptionEmbDistance'] = 1.0
        return df

    # Embed every description in one batched pass.
    all_embs = embed_model.encode(descriptions, batch_size=64, show_progress_bar=False,
                                  normalize_embeddings=True)

    # Build a lookup: BeneficiaryId → mean embedding of their own past descriptions
    # (nominations where they were the nominator).
    # This is the "nominee's voice" — we compare it against the nominator's text.
    ben_emb_map: dict = {}
    for user_id, group in df.groupby('BeneficiaryId'):
        indices  = group.index.tolist()
        user_embs = all_embs[df.index.get_indexer(indices)]
        if len(user_embs) > 0:
            ben_emb_map[user_id] = user_embs.mean(axis=0)

    cosine_sims   = []
    emb_distances = []

    for i, (idx, row) in enumerate(df.iterrows()):
        nom_emb = all_embs[i]
        ben_mean = ben_emb_map.get(row['BeneficiaryId'])

        if ben_mean is not None:
            sim  = float(sk_cosine_similarity([nom_emb], [ben_mean])[0][0])
            dist = float(np.linalg.norm(nom_emb - ben_mean))
        else:
            sim  = 0.0   # neutral — no beneficiary history to compare
            dist = 1.0

        cosine_sims.append(sim)
        emb_distances.append(dist)

    df['DescriptionCosineSim']   = cosine_sims
    df['DescriptionEmbDistance'] = emb_distances

    print(
        f"  Semantic features computed — "
        f"mean cosine sim: {np.mean(cosine_sims):.3f}, "
        f"mean emb distance: {np.mean(emb_distances):.3f}"
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

    # ── Graph pattern features ───────────────────────────────────────────────
    # Raw columns from load_data() are nominator/beneficiary split; compose
    # them into the single features the RF sees.
    #
    # GraphCycleFlag — 1 if nominator OR beneficiary is in a Ring finding.
    df['GraphCycleFlag'] = (
        (df.get('NominatorIsInRing', 0).fillna(0).astype(int))
        | (df.get('BeneficiaryIsInRing', 0).fillna(0).astype(int))
    ).astype(int)

    # GraphReciprocalFlag — HasReciprocalNomination already captures this at
    # the nomination level from the training set.  The graph flag version is
    # the same signal but sourced from UserGraphFlags at inference time.
    # At training time we reuse HasReciprocalNomination (already computed
    # above) so no duplicate column is needed; the RF column is aliased below.
    df['GraphReciprocalFlag'] = df['HasReciprocalNomination'].fillna(0).astype(int)

    # GraphClusterSize — take the maximum cluster size across nominator and
    # beneficiary (if either is in a copy-paste cluster, the cluster size
    # reflects their worst-case exposure).
    df['GraphClusterSize'] = df[
        ['NominatorClusterSize', 'BeneficiaryClusterSize']
    ].fillna(0).max(axis=1).astype(int)

    # SuperNominatorFlag — directly from nominator's snapshot.
    df['SuperNominatorFlag'] = df.get('SuperNominatorFlag', 0).fillna(0).astype(int)

    # TransactionalLanguageFlag — 1 if nominator OR beneficiary appears in a
    # TransactionalLanguage finding.
    df['TransactionalLanguageFlag'] = (
        (df.get('NominatorTransactional', 0).fillna(0).astype(int))
        | (df.get('BeneficiaryTransactional', 0).fillna(0).astype(int))
    ).astype(int)

    # ApproverAffinityFlag and GraphApproverPairCount come through directly
    # from load_data() — ensure they're int with no NULLs.
    df['ApproverAffinityFlag']   = df.get('ApproverAffinityFlag', 0).fillna(0).astype(int)
    df['GraphApproverPairCount'] = df.get('GraphApproverPairCount', 0).fillna(0).astype(int)

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
    Derive pseudo-labels when no confirmed P2P_FraudScores exist yet (cold start).

    Two-stage strategy:

    Stage 1 — Isolation Forest (primary):
        Trains an IsolationForest on P2P_FEATURE_COLUMNS with no labels.
        Anomalies are observations isolated in fewer random splits — exactly
        the property that makes fraudulent nominations stand out (unusual
        amounts, concentrated nominators, repeated pairs, copied descriptions).
        contamination=0.10 flags the most anomalous ~10% as IsFraud=1.
        These pseudo-labels bootstrap the first Random Forest regardless of
        whether the data is synthetic or real-world.

    Stage 2 — Graph findings (supplementary):
        Adds any nominations referenced by HIGH/CRITICAL GraphPatternFindings
        as additional IsFraud=1 labels on top of the IF output.  Graph
        findings capture structural patterns (rings, copy-paste clusters)
        that the IF may score less confidently due to feature representation.

    Returns None if fewer than MIN_FRAUD_BOOTSTRAP labels are found (the
    dataset is too clean or too small to train a meaningful model).
    """
    MIN_FRAUD_BOOTSTRAP = 5

    df = df.copy()
    df['IsFraud'] = 0

    # ── Stage 1: Isolation Forest ─────────────────────────────────────────────
    X = df[P2P_FEATURE_COLUMNS].fillna(0)

    iso = IsolationForest(
        n_estimators=200,
        contamination=0.10,   # assume ~10% anomaly rate as cold-start prior
        random_state=42,
        n_jobs=-1,
    )
    iso.fit(X)

    # predict() returns -1 (anomaly) or +1 (inlier)
    if_preds = iso.predict(X)
    df.loc[if_preds == -1, 'IsFraud'] = 1

    if_fraud_n = int(df['IsFraud'].sum())
    print(
        f"[Tenant {tenant_id}] ⚡ Isolation Forest pseudo-labels: "
        f"{if_fraud_n} anomalies ({if_fraud_n / len(df) * 100:.1f}%) "
        f"from {len(df)} nominations  (contamination=0.10)"
    )

    # ── Stage 2: graph findings supplement ────────────────────────────────────
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT NominationIds
            FROM   dbo.GraphPatternFindings
            WHERE  TenantId     = ?
              AND  Severity     IN ('Critical', 'High')
              AND  NominationIds IS NOT NULL
              AND  NominationIds != '[]'
        """, tenant_id)
        rows = cursor.fetchall()
        conn.close()

        graph_nom_ids: set[int] = set()
        for row in rows:
            graph_nom_ids.update(json.loads(row[0]))

        if graph_nom_ids:
            before = int(df['IsFraud'].sum())
            df.loc[df['NominationId'].isin(graph_nom_ids), 'IsFraud'] = 1
            added = int(df['IsFraud'].sum()) - before
            if added:
                print(
                    f"[Tenant {tenant_id}]   Graph findings added {added} label(s) "
                    f"from {len(rows)} HIGH/CRITICAL finding(s)"
                )
    except Exception as exc:
        print(f"[Tenant {tenant_id}] ⚠  Graph findings query failed (non-fatal): {exc}")

    fraud_n = int(df['IsFraud'].sum())
    legit_n = len(df) - fraud_n

    print(
        f"[Tenant {tenant_id}] ⚡ Bootstrap total: "
        f"{fraud_n} fraud ({fraud_n / len(df) * 100:.1f}%), {legit_n} legitimate"
    )

    if fraud_n < MIN_FRAUD_BOOTSTRAP:
        print(
            f"[Tenant {tenant_id}] ⚠  Only {fraud_n} fraud pseudo-label(s) — "
            f"need at least {MIN_FRAUD_BOOTSTRAP} for a meaningful model. "
            "Skipping. The backend will return UNKNOWN/MANUAL_REVIEW until "
            "more data accumulates."
        )
        return None

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
    'DescriptionCosineSim',
    'DescriptionEmbDistance',
    # Graph pattern features (point-in-time joined from dbo.UserGraphFlags)
    'GraphCycleFlag',               # nominator or beneficiary in a Ring finding
    'GraphReciprocalFlag',          # beneficiary has nominated nominator back
    'GraphClusterSize',             # CopyPaste cluster size (0 if not in cluster)
    'SuperNominatorFlag',           # nominator is a SuperNominator outlier
    'TransactionalLanguageFlag',    # nominator/beneficiary in TransactionalLanguage finding
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
    # Graph pattern features (point-in-time joined from dbo.UserGraphFlags
    # and dbo.ApproverPairFlags)
    'ApproverAffinityFlag',         # approver is in an ApproverAffinity finding
    'GraphApproverPairCount',       # times this approver approved this nominator→beneficiary pair
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

    Bulk-upsert strategy (temp table + single MERGE per table):
      1. Vectorize all scoring and flag logic in numpy — no Python row loop.
      2. Bulk-insert the full result set into a #staging temp table using
         fast_executemany=True (one network round-trip for the whole batch).
      3. Execute one MERGE statement per table against the staging data.
      Total SQL round-trips: 3 per table (CREATE, INSERT, MERGE) regardless
      of row count — vs. N round-trips in the old per-row approach.
    """
    import time
    t_total = time.perf_counter()

    n = len(df)
    print(f"\n[Tenant {tenant_id}] Scoring {n} historical nominations ...")

    # ── DB connection ─────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.fast_executemany = True   # batch driver calls in executemany
    print(f"[Tenant {tenant_id}]   DB connect:             {time.perf_counter() - t0:.2f}s")

    # ── P2P inference ─────────────────────────────────────────────────────────
    p2p_rf     = model_data['p2p_model']
    p2p_scaler = model_data['p2p_scaler']
    p2p_cols   = model_data['p2p_feature_columns']

    t0 = time.perf_counter()
    p2p_probas = p2p_rf.predict_proba(p2p_scaler.transform(df[p2p_cols].fillna(0)))
    print(f"[Tenant {tenant_id}]   P2P predict_proba:      {time.perf_counter() - t0:.2f}s  ({n} rows)")
    if p2p_probas.shape[1] < 2:
        print(f"[Tenant {tenant_id}] ⚠  P2P single-class model — skipping P2P score persistence.")
        p2p_fraud_probs = None
    else:
        p2p_fraud_probs = p2p_probas[:, 1]

    # ── Approver inference ────────────────────────────────────────────────────
    appr_rf     = model_data['appr_model']
    appr_scaler = model_data['appr_scaler']
    appr_cols   = model_data['appr_feature_columns']

    if appr_rf is None:
        print(f"[Tenant {tenant_id}] ⚠  No approver model — skipping approver score persistence.")
        appr_fraud_probs = None
    else:
        t0 = time.perf_counter()
        appr_probas = appr_rf.predict_proba(appr_scaler.transform(df[appr_cols].fillna(0)))
        print(f"[Tenant {tenant_id}]   Appr predict_proba:     {time.perf_counter() - t0:.2f}s  ({n} rows)")
        if appr_probas.shape[1] < 2:
            print(f"[Tenant {tenant_id}] ⚠  Approver single-class model — skipping approver score persistence.")
            appr_fraud_probs = None
        else:
            appr_fraud_probs = appr_probas[:, 1]

    # ── Vectorized score + flag computation (no Python row loop) ─────────────
    # np.select for risk levels; np.where string concat for flags.
    # np.char.rstrip strips any trailing ", " left by absent flag slots.
    t0 = time.perf_counter()

    def _risk_level_vec(scores: np.ndarray) -> np.ndarray:
        return np.select(
            [scores >= 80, scores >= 60, scores >= 40, scores >= 20],
            ['CRITICAL',   'HIGH',       'MEDIUM',      'LOW'],
            default='NONE',
        )

    nom_ids = df['NominationId'].astype(int).values

    p2p_rows: list | None = None
    if p2p_fraud_probs is not None:
        p2p_scores = (p2p_fraud_probs * 100).astype(int)
        p2p_levels = _risk_level_vec(p2p_scores)
        p2p_flags = pd.Series(
            np.where(df['PairNominationCount'].fillna(0).values > 5,
                     'Repeated beneficiary, ', '').astype(object)
            + np.where(df['HasReciprocalNomination'].fillna(0).values == 1,
                       'Reciprocal nomination detected, ', '').astype(object)
            + np.where(df['NominatorConcentrationRatio'].fillna(0).values > 5,
                       'Limited beneficiary diversity, ', '').astype(object)
            + np.where(df['IsHighAmount'].fillna(0).values == 1,
                       'Unusually high amount', '').astype(object)
        ).str.rstrip(', ')
        p2p_rows = list(zip(
            nom_ids.tolist(),
            p2p_scores.tolist(),
            p2p_levels.tolist(),
            p2p_flags.tolist(),
        ))

    appr_rows: list | None = None
    if appr_fraud_probs is not None:
        appr_scores = (appr_fraud_probs * 100).astype(int)
        appr_levels = _risk_level_vec(appr_scores)
        hours_to_pay = df['HoursToPayment'].fillna(0).values
        appr_flags = pd.Series(
            np.where(df['IsRapidApproval'].fillna(0).values == 1,
                     'Rapid approval, ', '').astype(object)
            + np.where((hours_to_pay > 0) & (hours_to_pay < 24),
                       'Fast payment', '').astype(object)
        ).str.rstrip(', ')
        appr_rows = list(zip(
            nom_ids.tolist(),
            appr_scores.tolist(),
            appr_levels.tolist(),
            appr_flags.tolist(),
        ))

    print(f"[Tenant {tenant_id}]   Vectorize scores/flags: {time.perf_counter() - t0:.2f}s")

    # ── P2P bulk upsert: CREATE temp → bulk INSERT → single MERGE ─────────────
    if p2p_rows:
        t0 = time.perf_counter()
        cursor.execute("""
            CREATE TABLE #p2p_staging (
                NominationId INT           NOT NULL,
                FraudScore   INT           NOT NULL,
                RiskLevel    NVARCHAR(20)  NOT NULL,
                FraudFlags   NVARCHAR(500)     NULL
            )
        """)
        cursor.executemany(
            "INSERT INTO #p2p_staging (NominationId, FraudScore, RiskLevel, FraudFlags) "
            "VALUES (?, ?, ?, ?)",
            p2p_rows,
        )
        print(f"[Tenant {tenant_id}]   P2P temp insert:        {time.perf_counter() - t0:.2f}s  ({len(p2p_rows)} rows)")

        t0 = time.perf_counter()
        cursor.execute("""
            MERGE dbo.P2P_FraudScores AS target
            USING #p2p_staging AS source
                ON target.NominationId = source.NominationId
            WHEN MATCHED THEN
                UPDATE SET FraudScore = source.FraudScore,
                           RiskLevel  = source.RiskLevel,
                           FraudFlags = source.FraudFlags
            WHEN NOT MATCHED THEN
                INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
                VALUES (source.NominationId, source.FraudScore,
                        source.RiskLevel,   source.FraudFlags);
        """)
        print(f"[Tenant {tenant_id}]   P2P MERGE:              {time.perf_counter() - t0:.2f}s")

    # ── Approver bulk upsert: CREATE temp → bulk INSERT → single MERGE ────────
    if appr_rows:
        t0 = time.perf_counter()
        cursor.execute("""
            CREATE TABLE #appr_staging (
                NominationId INT           NOT NULL,
                FraudScore   INT           NOT NULL,
                RiskLevel    NVARCHAR(20)  NOT NULL,
                FraudFlags   NVARCHAR(500)     NULL
            )
        """)
        cursor.executemany(
            "INSERT INTO #appr_staging (NominationId, FraudScore, RiskLevel, FraudFlags) "
            "VALUES (?, ?, ?, ?)",
            appr_rows,
        )
        print(f"[Tenant {tenant_id}]   Appr temp insert:       {time.perf_counter() - t0:.2f}s  ({len(appr_rows)} rows)")

        t0 = time.perf_counter()
        cursor.execute("""
            MERGE dbo.Appr_FraudScores AS target
            USING #appr_staging AS source
                ON target.NominationId = source.NominationId
            WHEN MATCHED THEN
                UPDATE SET FraudScore = source.FraudScore,
                           RiskLevel  = source.RiskLevel,
                           FraudFlags = source.FraudFlags
            WHEN NOT MATCHED THEN
                INSERT (NominationId, FraudScore, RiskLevel, FraudFlags)
                VALUES (source.NominationId, source.FraudScore,
                        source.RiskLevel,   source.FraudFlags);
        """)
        print(f"[Tenant {tenant_id}]   Appr MERGE:             {time.perf_counter() - t0:.2f}s")

    # ── Commit ────────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    conn.commit()
    print(f"[Tenant {tenant_id}]   commit():               {time.perf_counter() - t0:.2f}s")

    cursor.close()
    conn.close()

    p2p_n    = len(p2p_rows)  if p2p_rows  else 0
    appr_n   = len(appr_rows) if appr_rows else 0
    p2p_high = int(np.sum(p2p_fraud_probs * 100 >= 60)) if p2p_fraud_probs is not None else 0
    print(
        f"[Tenant {tenant_id}] ✓ P2P: {p2p_n} upserted ({p2p_high} HIGH/CRITICAL) | "
        f"Approver: {appr_n} upserted  |  "
        f"Total: {time.perf_counter() - t_total:.2f}s"
    )


def train_model(df: pd.DataFrame, tenant_id: int) -> tuple[dict, dict]:
    """
    Train a Random Forest for one tenant and persist it to
    Output/random_forest_tenant_{tenant_id}.pkl.
    """
    print(f"\n[Tenant {tenant_id}] Training model ...")

    # Load embedding model once per training run (shared across all tenants
    # in the same process to avoid reloading the ~90 MB weights repeatedly).
    embed_model_name = get_tenant_embed_model(tenant_id)
    print(f"[Tenant {tenant_id}]   Embed model: {embed_model_name}")
    embed_model = SentenceTransformer(embed_model_name)
    df = add_semantic_features(df, embed_model)

    df, category_fraud_rate, global_fraud_rate = extract_features(df)

    # If no P2P scores have been recorded yet (cold start), derive labels
    # from behavioural patterns.  If no patterns exist either (clean new
    # tenant), bootstrap_fraud_labels returns None — skip gracefully.
    if df['IsFraud'].sum() == 0:
        print(
            f"[Tenant {tenant_id}] ⚠  No P2P fraud labels found — "
            "bootstrapping from behavioural patterns."
        )
        df = bootstrap_fraud_labels(df, tenant_id)
        if df is None:
            return None, {'skipped': True}

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

    def _train_rf(X: pd.DataFrame, label: str, y_: pd.Series = None) -> tuple:
        """Train one RF, print evaluation, return (model, scaler, auc)."""
        y_use = y_ if y_ is not None else y
        print(f"\n[Tenant {tenant_id}] Training {label} model — shape: {X.shape}")
        X_tr, X_te, y_tr, y_te = train_test_split(
            X, y_use, test_size=0.2, random_state=42, stratify=y_use
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
    # Restrict to Approved/Paid: HoursToApproval and HoursToPayment are only
    # meaningful when both ApprovedDate and PayedDate are populated.  Pending
    # nominations have NULL timestamps and would produce misleading zero values
    # after fillna(0), skewing the approver model's rapid-approval signal.
    df_appr = df_train[df_train['Status'].isin(['Approved', 'Paid'])].copy()

    p2p_probas_appr = p2p_rf.predict_proba(
        p2p_scaler.transform(df_appr[P2P_FEATURE_COLUMNS].fillna(0))
    )[:, 1]
    df_appr['IsApproverFraud'] = (
        pd.Series((p2p_probas_appr * 100 >= 60).astype(int), index=df_appr.index)
        & (df_appr['IsRapidApproval'] == 1)
    ).astype(int)

    appr_auc = None
    if df_appr['IsApproverFraud'].sum() > 0:
        appr_rf, appr_scaler, appr_auc = _train_rf(
            df_appr[APPR_FEATURE_COLUMNS].fillna(0), "Approver",
            y_=df_appr['IsFraud'],
        )
    else:
        print(
            f"[Tenant {tenant_id}] ⚠  No approver-fraud labels derived — "
            "skipping approver model training. "
            "Need HIGH/CRITICAL P2P nominations with IsRapidApproval=1."
        )
        appr_rf     = None
        appr_scaler = None

    # ── Persist pkl ───────────────────────────────────────────────────────────
    model_version = f"rf-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-t{tenant_id}"
    model_data = {
        'model_version':         model_version,
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
        # Sentence-transformer model name — fraud_ml.py loads the same model
        'embed_model_name':     embed_model_name,
    }

    pkl_filename = OUTPUT_DIR / f"random_forest_tenant_{tenant_id}.pkl"
    with open(pkl_filename, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\n✓ Model saved to '{pkl_filename}'")
    _upload_artefact(pkl_filename)

    # ── Score all historical nominations into both score tables ───────────────
    score_and_save_historical(df, model_data, tenant_id)

    # ── Visualisations ────────────────────────────────────────────────────────
    # Compute probability arrays from the already-loaded df so we don't need
    # a second DB round-trip and so column names are unambiguous.
    p2p_probs_viz = p2p_rf.predict_proba(
        p2p_scaler.transform(df[P2P_FEATURE_COLUMNS].fillna(0))
    )[:, 1]
    appr_probs_viz = (
        appr_rf.predict_proba(
            appr_scaler.transform(df[APPR_FEATURE_COLUMNS].fillna(0))
        )[:, 1]
        if appr_rf is not None else None
    )
    create_visualizations(df, p2p_probs_viz, appr_probs_viz, tenant_id)

    return model_data, {
        'p2p_auc': p2p_auc, 'appr_auc': appr_auc,
        'training_samples': len(df_train), 'model_version': model_version,
    }


# ============================================================================
# VISUALISATIONS
# ============================================================================

def create_visualizations(
    df: pd.DataFrame,
    p2p_probs: np.ndarray,
    appr_probs: np.ndarray,
    tenant_id: int,
) -> None:
    """
    Generate and upload a fraud score distribution chart showing P2P and
    Approver score distributions side by side, coloured by IsFraud label.
    Probabilities are passed in directly from the training run — no DB re-fetch.
    """
    print(f"\n[Tenant {tenant_id}] Creating visualisations ...")

    is_fraud = df['IsFraud'].fillna(0).astype(int).values

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"Fraud Score Distribution — Tenant {tenant_id}", fontsize=13)

    for ax, (probs, label) in zip(axes, [
        (p2p_probs,  'P2P'),
        (appr_probs, 'Approver'),
    ]):
        if probs is None:
            ax.text(0.5, 0.5, 'No approver model\n(insufficient labelled data)',
                    ha='center', va='center', transform=ax.transAxes,
                    fontsize=11, color='grey')
            ax.set_title(f'{label} Score Distribution')
            ax.axis('off')
            continue
        scores = (probs * 100).astype(int)
        legit  = scores[is_fraud == 0]
        fraud  = scores[is_fraud == 1]
        ax.hist([legit, fraud], bins=30, label=['Legitimate', 'Fraud'], alpha=0.7)
        ax.set_xlabel('Fraud Score (0–100)')
        ax.set_ylabel('Count')
        ax.set_title(f'{label} Score Distribution')
        ax.legend()

    plt.tight_layout()
    png_filename = OUTPUT_DIR / f"random_forest_tenant_{tenant_id}.png"
    plt.savefig(png_filename, dpi=300, bbox_inches='tight')
    print(f"✓ Visualisation saved to '{png_filename}'")
    _upload_artefact(png_filename)
    plt.close()


# ============================================================================
# MAIN — iterate over all tenants
# ============================================================================

def _record_rf_status(**kwargs) -> None:
    """Write RF producer status with a short, independent DB connection."""
    status_conn = get_db_connection()
    try:
        upsert_component_status(status_conn, component="RF", **kwargs)
    finally:
        status_conn.close()


def main(tenants_to_process: list | None = None) -> None:
    """Entry point called by run_job.py (Stage 1)."""
    print("=" * 60)
    print("FRAUD DETECTION MODEL TRAINING  —  Multi-Tenant")
    print("=" * 60)

    conn = get_db_connection()
    tenants = get_tenants(conn)
    conn.close()

    if tenants_to_process is not None:
        tenants = [t for t in tenants if t[0] in tenants_to_process]
        if not tenants:
            print(f"⚠  Tenant(s) {tenants_to_process} not found in database. Exiting.")
            return

    print(f"\nFound {len(tenants)} tenant(s): {[t[0] for t in tenants]}")

    run_id = str(uuid.uuid4())
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
                _record_rf_status(
                    tenant_id=tenant_id, attempt_status="SKIPPED",
                    reason_code="BELOW_MINIMUM_VOLUME",
                    reason_detail=(f"{len(df)} nominations; requires "
                                   f"{MIN_TRAINING_SAMPLES}"),
                    diagnostics={
                        "nomination_count": len(df),
                        "minimum_training_samples": MIN_TRAINING_SAMPLES,
                    },
                    run_id=run_id,
                )
                continue

            model_data, stats = train_model(df, tenant_id)
            if stats.get('skipped'):
                print(f"⚠  Tenant {tenant_id} skipped — no fraud patterns found.")
                results[tenant_id] = "SKIPPED (no fraud patterns — model will be trained once patterns emerge)"
                _record_rf_status(
                    tenant_id=tenant_id, attempt_status="SKIPPED",
                    reason_code="NO_FRAUD_PATTERNS",
                    reason_detail="No bootstrap fraud patterns were available to train the RF.",
                    diagnostics={"nomination_count": len(df)}, run_id=run_id,
                )
                continue
            p2p_auc_str  = f"{stats['p2p_auc']:.4f}"  if stats.get('p2p_auc')  else "n/a"
            appr_auc_str = f"{stats['appr_auc']:.4f}" if stats.get('appr_auc') else "n/a"
            results[tenant_id] = (
                f"OK  ({stats['training_samples']} samples, "
                f"P2P AUC={p2p_auc_str}, Approver AUC={appr_auc_str})"
            )
            _record_rf_status(
                tenant_id=tenant_id, attempt_status="SUCCEEDED",
                serving_status="AVAILABLE",
                serving_version=model_data.get("model_version"),
                diagnostics={
                    "training_samples": stats["training_samples"],
                    "p2p_auc": stats.get("p2p_auc"),
                    "approver_auc": stats.get("appr_auc"),
                },
                run_id=run_id,
            )

        except Exception as exc:
            import traceback
            print(f"❌  Tenant {tenant_id} failed: {exc}")
            print(traceback.format_exc())
            results[tenant_id] = f"FAILED — {exc}"
            failed.append(tenant_id)
            try:
                _record_rf_status(
                    tenant_id=tenant_id, attempt_status="FAILED",
                    reason_code="TRAINING_FAILED", reason_detail=str(exc),
                    run_id=run_id,
                )
            except Exception:
                print(f"❌  Tenant {tenant_id} RF failure status could not be persisted")

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
