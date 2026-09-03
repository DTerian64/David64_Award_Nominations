"""
train_rf_model.py — Random Forest Training, Multi-Tenant Edition
================================================================

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
the same filename. The backend RandomForestModelCache streams models directly from
blob (no local copy); idle models are evicted from the in-process cache after
MODEL_IDLE_TTL_SECONDS.  The next request after eviction re-streams the blob,
so fresh models propagate automatically within one TTL period of upload — no
container restart required.
"""

import os
import re
import uuid
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import pickle
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.metrics.pairwise import cosine_similarity as sk_cosine_similarity
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from pathlib import Path
from sentence_transformers import SentenceTransformer

try:
    from . import labels as labels_mod
    from .artifact_manifest import (
        MANIFEST_SCHEMA_VERSION,
        artifact_descriptor,
        write_manifest,
    )
except ImportError:  # pragma: no cover - standalone ``python modeling/...`` path
    import labels as labels_mod
    from artifact_manifest import (
        MANIFEST_SCHEMA_VERSION,
        artifact_descriptor,
        write_manifest,
    )
from utils.component_status import upsert_component_status

# Default sentence-transformer model for English tenants.
# Per-tenant overrides are read from dbo.Tenants.desc_check_config at
# training time and stored in the pkl so inference/random_forest_check.py loads the same
# model at inference time (e.g. 'paraphrase-multilingual-MiniLM-L12-v2'
# for Korean / Japanese / other non-English tenants).
DEFAULT_EMBED_MODEL_NAME = 'all-MiniLM-L6-v2'
RF_FEATURE_CONTRACT = 'rf-native-v3'
TRANSACTIONAL_PHRASE_RULE_VERSION = 'transactional-phrase-score-v1'
_TRANSACTIONAL_PHRASE_REFERENCE_HITS = 6.0
_TRANSACTIONAL_PHRASE_PATTERN = re.compile(
    r'\b(?:'
    r'helped me|help me|'
    r'my deadline|our deadline|'
    r'saved my|saved the day|'
    r'owe[sd]? (?:him|her|them|me)|'
    r'in return|return the favor|'
    r'scratch my back|you scratch|'
    r'promised|will nominate|going to nominate|'
    r'nominate (?:you|him|her|them) (?:next|back|in return)|'
    r'my project|my task|my work'
    r')\b',
    re.IGNORECASE,
)


def transactional_phrase_score(description: str | None) -> float:
    """Return the RF-owned continuous 0..1 phrase feature."""
    hits = sum(
        1 for _match in _TRANSACTIONAL_PHRASE_PATTERN.finditer(description or '')
    )
    return round(min(hits / _TRANSACTIONAL_PHRASE_REFERENCE_HITS, 1.0), 4)

JOB_DIR = Path(__file__).resolve().parents[1]
env_path = JOB_DIR.parent / ".env"
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
# Five per class keeps both classes represented in the 20% stratified holdout.
MIN_TRAINING_CLASS_SAMPLES = 5
RF_BOOTSTRAP_SOURCE = 'rf_bootstrap'

# All generated artefacts (.pkl models, .png charts) are written here.
OUTPUT_DIR = JOB_DIR / "Output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _rf_classifier_summary(model, feature_columns: list[str]) -> dict:
    """Build a JSON-safe structural summary of one fitted RF classifier."""
    if model is None:
        return {"available": False, "feature_count": len(feature_columns), "features": []}

    trees = list(model.estimators_)
    params = model.get_params(deep=False)
    features = sorted(
        (
            {"name": name, "importance": float(importance)}
            for name, importance in zip(feature_columns, model.feature_importances_)
        ),
        key=lambda item: item["importance"],
        reverse=True,
    )
    depths = [int(tree.tree_.max_depth) for tree in trees]
    node_counts = [int(tree.tree_.node_count) for tree in trees]
    return {
        "available": True,
        "estimator": type(model).__name__,
        "classes": [str(value) for value in model.classes_],
        "feature_count": len(feature_columns),
        "tree_count": len(trees),
        "hyperparameters": {
            key: params.get(key)
            for key in (
                "n_estimators", "max_depth", "min_samples_split",
                "min_samples_leaf", "class_weight", "random_state",
            )
        },
        "tree_statistics": {
            "minimum_depth": min(depths),
            "maximum_depth": max(depths),
            "average_depth": sum(depths) / len(depths),
            "minimum_nodes": min(node_counts),
            "maximum_nodes": max(node_counts),
            "average_nodes": sum(node_counts) / len(node_counts),
        },
        "features": features,
    }


def _write_rf_manifest(
    tenant_id: int,
    model_data: dict,
    training_metrics: dict,
    pkl_path: Path,
    png_path: Path,
) -> Path:
    """Publish a non-executable representation of the RF training output."""
    manifest_path = OUTPUT_DIR / f"random_forest_tenant_{tenant_id}.manifest.json"
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_type": "random_forest",
        "tenant_id": tenant_id,
        "model_version": model_data["model_version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "Tenant-scoped Random Forest nomination model",
        "models": {
            "p2p": _rf_classifier_summary(
                model_data["p2p_model"], model_data["p2p_feature_columns"]
            ),
        },
        "training": training_metrics,
        "data_profile": {
            "feature_contract": model_data["feature_contract"],
            "amount_mean": model_data["amount_mean"],
            "amount_std": model_data["amount_std"],
            "category_count": len(model_data["category_fraud_rate"]),
            "global_fraud_rate": model_data["global_fraud_rate"],
            "embedding_model": model_data["embed_model_name"],
            "transactional_phrase_rule_version": model_data[
                "transactional_phrase_rule_version"
            ],
        },
        "artifacts": [
            artifact_descriptor(pkl_path, "serving_model"),
            artifact_descriptor(png_path, "score_distribution"),
        ],
    }
    write_manifest(manifest_path, manifest)
    return manifest_path


# ============================================================================
# DATABASE CONNECTION
# ============================================================================

from utils.db_conn import connect  # noqa: E402 - .env must load before credential setup


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
      • HRBP FRAUD / LEGITIMATE dispositions — authoritative shared human labels.
      • HRBP EXCLUDED dispositions — retained for audit but omitted from training.
      • Unreviewed rows may participate only in the RF's independent cold-start
        bootstrap; prior model predictions are never supervised ground truth.
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
        n.Amount,
        n.Currency,
        n.NominationDescription,
        n.NominationDate,
        n.Status,
        n.CategoryId

    FROM dbo.Nominations n
    JOIN dbo.Users u ON u.UserId = n.NominatorId
    WHERE n.Status NOT IN ('PendingHRBPReview')
      AND NOT (n.Status = 'Rejected' AND n.RejectionActor = 'Fraud Detection (Description)')
      AND u.TenantId = ?
    ORDER BY n.NominationDate
    """

    df = pd.read_sql(query, conn, params=[tenant_id])
    label_df = labels_mod.load_labels(conn, tenant_id)
    conn.close()

    # One model-neutral label contract feeds both RF and GNN. The RF still owns
    # its feature engineering and cold-start bootstrap; it does not own HRBP
    # ground truth or consume a GNN prediction.
    df = labels_mod.attach_training_labels(df, label_df)

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

    # RF-native text feature. It is calculated from the nomination description
    # here and independently at inference; no Graph snapshot is consulted.
    df['TransactionalPhraseScore'] = (
        df['NominationDescription'].fillna('').map(transactional_phrase_score)
    )

    # ── Date parsing ────────────────────────────────────────────────────────
    df['NominationDate'] = pd.to_datetime(df['NominationDate'])

    # ── Temporal features ───────────────────────────────────────────────────
    df['DayOfWeek'] = df['NominationDate'].dt.dayofweek
    df['Month']     = df['NominationDate'].dt.month
    df['Hour']      = df['NominationDate'].dt.hour
    df['IsWeekend'] = df['DayOfWeek'].isin([5, 6]).astype(int)

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
    Derive RF-only pseudo-labels while human labels are insufficient.

    Isolation Forest strategy:
        Trains an IsolationForest on P2P_FEATURE_COLUMNS with no labels.
        Anomalies are observations isolated in fewer random splits — exactly
        the property that makes fraudulent nominations stand out (unusual
        amounts, concentrated nominators, repeated pairs, copied descriptions).
        contamination=0.10 flags the most anomalous ~10% as IsFraud=1.
        These pseudo-labels bootstrap the first Random Forest regardless of
        whether the data is synthetic or real-world.

    Human labels are preserved; explicit exclusions remain unlabelled. The
    caller checks volume and class balance after combining human/pseudo labels.
    LabelSource is changed only on this in-memory copy, never in the database.
    """
    df = df.copy()
    excluded_mask = df['LabelSource'].eq(labels_mod.SOURCE_EXCLUDED)
    df.loc[excluded_mask, 'IsFraud'] = pd.NA
    bootstrap_index = df.index[
        df['LabelSource'].eq(labels_mod.SOURCE_UNLABELLED)
    ]
    if len(bootstrap_index) == 0:
        print(f"[Tenant {tenant_id}] No unreviewed nominations are available for bootstrap.")
        return df
    df.loc[bootstrap_index, 'IsFraud'] = 0

    # RF cold-start labels are derived only from its own feature space. Graph
    # findings are deliberately excluded so cross-engine agreement remains
    # meaningful after the synthetic-data reset.
    X = df.loc[bootstrap_index, P2P_FEATURE_COLUMNS].fillna(0)

    iso = IsolationForest(
        n_estimators=200,
        contamination=0.10,   # assume ~10% anomaly rate as cold-start prior
        random_state=42,
        n_jobs=-1,
    )
    iso.fit(X)

    # predict() returns -1 (anomaly) or +1 (inlier)
    if_preds = iso.predict(X)
    anomaly_index = bootstrap_index[if_preds == -1]
    df.loc[anomaly_index, 'IsFraud'] = 1
    df.loc[bootstrap_index, 'LabelSource'] = RF_BOOTSTRAP_SOURCE

    if_fraud_n = len(anomaly_index)
    print(
        f"[Tenant {tenant_id}] Isolation Forest pseudo-labels: "
        f"{if_fraud_n} anomalies ({if_fraud_n / len(bootstrap_index) * 100:.1f}%) "
        f"from {len(bootstrap_index)} unreviewed nominations  (contamination=0.10)"
    )

    fraud_n = int(df['IsFraud'].sum())
    eligible_count = int(df['IsFraud'].notna().sum())
    legit_n = eligible_count - fraud_n

    print(
        f"[Tenant {tenant_id}] Bootstrap total: "
        f"{fraud_n} fraud ({fraud_n / eligible_count * 100:.1f}%), "
        f"{legit_n} legitimate, {int(excluded_mask.sum())} excluded"
    )

    return df


def prepare_rf_training_data(
    df: pd.DataFrame, tenant_id: int,
) -> tuple[pd.DataFrame | None, dict]:
    """Select supervised vs RF bootstrap training without changing source data."""
    df = df.copy()
    human = df['LabelSource'].eq(labels_mod.SOURCE_HRBP)
    if not df.loc[human, 'IsFraud'].isin([0, 1]).all():
        raise ValueError('Human RF training labels must be 0 or 1')
    # Neither legacy model outputs nor explicit exclusions are training targets.
    df.loc[~human, 'IsFraud'] = pd.NA
    human_count = int(human.sum())
    human_fraud = int(df.loc[human, 'IsFraud'].eq(1).sum())
    human_legitimate = human_count - human_fraud
    unlabelled_count = int(df['LabelSource'].eq(labels_mod.SOURCE_UNLABELLED).sum())
    supervised = (
        human_count >= MIN_TRAINING_SAMPLES
        and min(human_fraud, human_legitimate) >= MIN_TRAINING_CLASS_SAMPLES
    )
    diagnostics = {
        'nomination_count': len(df),
        'human_label_count': human_count,
        'human_fraud_count': human_fraud,
        'human_legitimate_count': human_legitimate,
        'unlabelled_count': unlabelled_count,
        'excluded_count': int(df['LabelSource'].eq(labels_mod.SOURCE_EXCLUDED).sum()),
        'training_mode': 'SUPERVISED' if supervised else (
            'BOOTSTRAP_HYBRID' if human_count else 'BOOTSTRAP'
        ),
        'minimum_training_samples': MIN_TRAINING_SAMPLES,
        'minimum_class_samples': MIN_TRAINING_CLASS_SAMPLES,
    }
    if not supervised and unlabelled_count and human_count + unlabelled_count >= MIN_TRAINING_SAMPLES:
        df = bootstrap_fraud_labels(df, tenant_id)

    pseudo = df['LabelSource'].eq(RF_BOOTSTRAP_SOURCE) & df['IsFraud'].notna()
    training = df.loc[(human | pseudo) & df['IsFraud'].notna()].copy()
    training['IsFraud'] = training['IsFraud'].astype(int)
    diagnostics.update({
        'pseudo_label_count': int(pseudo.sum()),
        'pseudo_fraud_count': int(df.loc[pseudo, 'IsFraud'].eq(1).sum()),
        'pseudo_legitimate_count': int(df.loc[pseudo, 'IsFraud'].eq(0).sum()),
        'training_samples': len(training),
        'training_fraud_count': int(training['IsFraud'].eq(1).sum()),
        'training_legitimate_count': int(training['IsFraud'].eq(0).sum()),
    })
    print(f'[Tenant {tenant_id}] RF training labels: {diagnostics}')
    if (
        len(training) < MIN_TRAINING_SAMPLES
        or min(diagnostics['training_fraud_count'], diagnostics['training_legitimate_count'])
        < MIN_TRAINING_CLASS_SAMPLES
    ):
        return None, {
            **diagnostics,
            'skipped': True,
            'reason_code': 'BOOTSTRAP_UNAVAILABLE',
            'reason_detail': (
                f'{human_count} human labels + {diagnostics["pseudo_label_count"]} RF pseudo-labels; '
                f'{diagnostics["training_fraud_count"]} fraud / '
                f'{diagnostics["training_legitimate_count"]} legitimate. '
                f'Requires {MIN_TRAINING_SAMPLES} labels and at least '
                f'{MIN_TRAINING_CLASS_SAMPLES} of each class. '
                'More eligible nominations or human adjudications are needed; '
                'the existing serving model is unchanged.'
            ),
        }
    return training, diagnostics


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
    'TransactionalPhraseScore',
]


def train_model(
    df: pd.DataFrame,
    tenant_id: int,
    tenant_name: str | None = None,
) -> tuple[dict | None, dict]:
    """
    Train a Random Forest for one tenant and persist it to
    Output/random_forest_tenant_{tenant_id}.pkl.
    """
    print(f"\n[Tenant {tenant_id}] Training model ...")

    if len(df) < MIN_TRAINING_SAMPLES:
        _, diagnostics = prepare_rf_training_data(df, tenant_id)
        return None, diagnostics

    # Load embedding model once per training run (shared across all tenants
    # in the same process to avoid reloading the ~90 MB weights repeatedly).
    embed_model_name = get_tenant_embed_model(tenant_id)
    print(f"[Tenant {tenant_id}]   Embed model: {embed_model_name}")
    embed_model = SentenceTransformer(embed_model_name)
    df = add_semantic_features(df, embed_model)

    df, category_fraud_rate, global_fraud_rate = extract_features(df)

    df_train, label_diagnostics = prepare_rf_training_data(df, tenant_id)
    if df_train is None:
        return None, label_diagnostics
    n_fraud = int(df_train['IsFraud'].sum())
    n_legit = int((df_train['IsFraud'] == 0).sum())

    label_diagnostics['evaluation_basis'] = (
        'HUMAN_LABEL_HOLDOUT' if label_diagnostics['training_mode'] == 'SUPERVISED'
        else 'BOOTSTRAP_LABEL_HOLDOUT_NOT_INDEPENDENT'
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

    def _train_rf(X: pd.DataFrame) -> tuple:
        """Train the nomination-time RF and return (model, scaler, AUC)."""
        print(f"\n[Tenant {tenant_id}] Training P2P model — shape: {X.shape}")
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
        print(f"P2P MODEL EVALUATION — Tenant {tenant_id} — {label_diagnostics['evaluation_basis']}")
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
        print("\nTop 10 Most Important P2P Features:")
        print(fi.head(10).to_string(index=False))

        return rf_, scaler_, auc_

    # ── Train P2P model ───────────────────────────────────────────────────────
    p2p_rf, p2p_scaler, p2p_auc = _train_rf(
        df_train[P2P_FEATURE_COLUMNS].fillna(0)
    )

    # ── Persist pkl ───────────────────────────────────────────────────────────
    model_version = f"rf-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-t{tenant_id}"
    model_data = {
        'model_version':         model_version,
        'feature_contract':      RF_FEATURE_CONTRACT,
        'training_label_summary': dict(label_diagnostics),
        # P2P model — used for live submission-time fraud scoring
        'p2p_model':            p2p_rf,
        'p2p_scaler':           p2p_scaler,
        'p2p_feature_columns':  P2P_FEATURE_COLUMNS,
        # Tenant-scoped amount stats for z-score computation at inference time
        'amount_mean':          float(df['Amount'].mean()),
        'amount_std':           float(df['Amount'].std()),
        # Category target encoding
        'category_fraud_rate':  category_fraud_rate,
        'global_fraud_rate':    float(global_fraud_rate),
        # Sentence-transformer model name — integrity-check inference loads the same model
        'embed_model_name':     embed_model_name,
        'transactional_phrase_rule_version': TRANSACTIONAL_PHRASE_RULE_VERSION,
    }

    pkl_filename = OUTPUT_DIR / f"random_forest_tenant_{tenant_id}.pkl"
    with open(pkl_filename, 'wb') as f:
        pickle.dump(model_data, f)

    print(f"\n✓ Model saved to '{pkl_filename}'")
    _upload_artefact(pkl_filename)

    # ── Visualisations ────────────────────────────────────────────────────────
    # Compute probability arrays from the already-loaded df so we don't need
    # a second DB round-trip and so column names are unambiguous.
    p2p_probs_viz = p2p_rf.predict_proba(
        p2p_scaler.transform(df_train[P2P_FEATURE_COLUMNS].fillna(0))
    )[:, 1]
    create_visualizations(df_train, p2p_probs_viz, tenant_id, tenant_name)

    training_metrics = {
        **label_diagnostics,
        'p2p_auc': p2p_auc,
        'training_samples': len(df_train), 'model_version': model_version,
    }
    manifest_path = _write_rf_manifest(
        tenant_id=tenant_id,
        model_data=model_data,
        training_metrics=training_metrics,
        pkl_path=pkl_filename,
        png_path=OUTPUT_DIR / f"random_forest_tenant_{tenant_id}.png",
    )
    _upload_artefact(manifest_path)

    return model_data, training_metrics


# ============================================================================
# VISUALISATIONS
# ============================================================================

def create_visualizations(
    df: pd.DataFrame,
    p2p_probs: np.ndarray,
    tenant_id: int,
    tenant_name: str | None = None,
) -> None:
    """
    Generate and upload the nomination-time RF score distribution, coloured by
    the human-confirmed/pseudo-label training outcome.
    """
    print(f"\n[Tenant {tenant_id}] Creating visualisations ...")

    is_fraud = df['IsFraud'].fillna(0).astype(int).values

    fig, ax = plt.subplots(figsize=(9, 5))
    scores = (p2p_probs * 100).astype(int)
    legitimate = scores[is_fraud == 0]
    fraud = scores[is_fraud == 1]
    ax.hist([legitimate, fraud], bins=30, label=['Legitimate', 'Fraud'], alpha=0.7)
    ax.set_xlabel('Fraud Score (0–100)')
    ax.set_ylabel('Count')
    tenant_label = tenant_name or f'Tenant {tenant_id}'
    ax.set_title(f'Nomination Fraud Score Distribution — {tenant_label}')
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

            model_data, stats = train_model(df, tenant_id, tenant_name)
            if stats.get('skipped'):
                print(f"⚠  Tenant {tenant_id} skipped — {stats['reason_detail']}")
                results[tenant_id] = f"SKIPPED ({stats['reason_code']})"
                _record_rf_status(
                    tenant_id=tenant_id, attempt_status="SKIPPED",
                    reason_code=stats['reason_code'],
                    reason_detail=stats['reason_detail'],
                    diagnostics={key: value for key, value in stats.items()
                                 if key not in ('skipped', 'reason_code', 'reason_detail')},
                    run_id=run_id,
                )
                continue
            p2p_auc_str  = f"{stats['p2p_auc']:.4f}"  if stats.get('p2p_auc')  else "n/a"
            results[tenant_id] = (
                f"OK  ({stats['training_samples']} samples, "
                f"P2P AUC={p2p_auc_str})"
            )
            _record_rf_status(
                tenant_id=tenant_id, attempt_status="SUCCEEDED",
                serving_status="AVAILABLE",
                serving_version=model_data.get("model_version"),
                diagnostics=stats,
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
