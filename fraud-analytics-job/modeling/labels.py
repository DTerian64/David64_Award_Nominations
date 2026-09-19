"""
labels.py — one definition of "fraud" for every model
======================================================

Both the Random Forest and the GNN need a training label. Model-neutral outcomes
live in dbo.IntegrityDecisionResults so neither model owns the ground truth.
Component scores remain immutable evidence that can be compared with the
adjudicated or synthetic outcome.

    FRAUD      -> IsFraud = 1
    LEGITIMATE -> IsFraud = 0
    EXCLUDED   -> IsFraud = NULL
    NULL       -> IsFraud = NULL

Inference risk and score fields are deliberately absent from this mapping.

    LabelSource   'hrbp'        eligible human investigation or random audit
                  'synthetic_ground_truth'
                                eligible only when Tenants.is_synthetic = 1
                  'excluded'    reviewed by HRBP, deliberately not a label
                  'model'       deprecated compatibility value; never emitted
                                by the canonical loader
                  'unlabelled'  no human disposition; IsFraud is NULL

Training behavior
-----------------
Eligible human labels and explicitly isolated synthetic ground truth are the
only supervised targets. Explicitly excluded reviews and unreviewed rows remain
NULL in this shared contract. Neither the Tabular family nor GNN turns model
output into training ground truth.

GNN and both Tabular candidates require eligible model-neutral labels or skip
the tenant under their respective sample gates.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# SOURCE_MODEL remains only so parity/audit callers can classify legacy frames;
# load_labels() never emits it.
SOURCE_HRBP       = "hrbp"
SOURCE_SYNTHETIC  = "synthetic_ground_truth"
SOURCE_EXCLUDED   = "excluded"
SOURCE_MODEL      = "model"
SOURCE_UNLABELLED = "unlabelled"

# Shared inclusion rules for model-neutral supervised outcomes.
#
#   PendingHRBPReview                     excluded — no confirmed label yet
#   Rejected by 'Fraud Detection (Description)'
#                                         excluded — Check A description quality
#                                         gate, not a fraud signal
#   Rejected by 'HRBP Review'             INCLUDED — the most valuable labels there are
#   everything else                       included
_INCLUSION_SQL = """
      n.Status NOT IN ('PendingHRBPReview')
  AND NOT (n.Status = 'Rejected' AND n.RejectionActor = 'Fraud Detection (Description)')
"""


def load_labels(
    conn,
    tenant_id: int,
    window_days: int | None = None,
) -> pd.DataFrame:
    """
    Return one row per in-scope nomination for the tenant.

    Columns
    -------
    NominationId  int
    IsFraud       nullable int — 0/1 for human labels, NULL otherwise
    LabelSource   str   'hrbp' | 'synthetic_ground_truth' | 'excluded' |
                        'model' | 'unlabelled'
    RiskLevel     str   the model's own risk level, preserved even where a
                        human has overridden the label
    ConfirmedBy   str   HRBP actor when reviewed, else None
    ConfirmedAt   datetime | None
    TrainingDisposition str | None
    ScenarioFamily str | None  explicit synthetic diagnostic metadata

    window_days=None loads the tenant's full history, matching load_data(), which
    has no date filter. The GNN passes a window; the Random Forest does not.

    IntegrityDecisionResults is the authoritative, model-neutral adjudication
    contract. Inference scores are retained for audit but never become labels.
    """
    window_clause = (
        "AND n.NominationDate >= DATEADD(DAY, -?, GETDATE())"
        if window_days is not None else ""
    )

    query = f"""
        SELECT
            n.NominationId,
            idr.CompositeRiskLevel AS RiskLevel,
            idr.ReviewedBy AS ConfirmedBy,
            idr.ReviewedAt AS ConfirmedAt,
            idr.TrainingDisposition,
            idr.TrainingDispositionSource,
            idr.TrainingDispositionMetadataJson,
            CASE WHEN ISJSON(idr.TrainingDispositionMetadataJson) = 1
                 THEN JSON_VALUE(
                     idr.TrainingDispositionMetadataJson, '$.scenario_family'
                 ) END AS ScenarioFamily,
            CASE WHEN ISJSON(idr.TrainingDispositionMetadataJson) = 1
                 THEN JSON_VALUE(
                     idr.TrainingDispositionMetadataJson, '$.scenario_variant'
                 ) END AS ScenarioVariant,
            CAST(t.is_synthetic AS INT) AS IsSyntheticTenant,
            CASE
                WHEN idr.TrainingDisposition = 'FRAUD'
                 AND (
                    idr.TrainingDispositionSource IN (
                        'HUMAN_INVESTIGATION', 'RANDOM_AUDIT'
                    )
                    OR (idr.TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'
                        AND t.is_synthetic = 1)
                 ) THEN 1
                WHEN idr.TrainingDisposition = 'LEGITIMATE'
                 AND (
                    idr.TrainingDispositionSource IN (
                        'HUMAN_INVESTIGATION', 'RANDOM_AUDIT'
                    )
                    OR (idr.TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'
                        AND t.is_synthetic = 1)
                 ) THEN 0
                ELSE NULL
            END AS IsFraud,
            CASE
                WHEN idr.TrainingDisposition IN ('FRAUD', 'LEGITIMATE')
                 AND idr.TrainingDispositionSource IN (
                    'HUMAN_INVESTIGATION', 'RANDOM_AUDIT'
                 )
                    THEN '{SOURCE_HRBP}'
                WHEN idr.TrainingDisposition IN ('FRAUD', 'LEGITIMATE')
                 AND idr.TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'
                 AND t.is_synthetic = 1
                    THEN '{SOURCE_SYNTHETIC}'
                WHEN idr.TrainingDisposition = 'EXCLUDED'
                    THEN '{SOURCE_EXCLUDED}'
                ELSE '{SOURCE_UNLABELLED}'
            END AS LabelSource,
            CASE
                WHEN idr.TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'
                 AND t.is_synthetic = 0 THEN 1 ELSE 0
            END AS InvalidSyntheticSource
        FROM       dbo.Nominations n
        JOIN       dbo.Users u   ON u.UserId       = n.NominatorId
        JOIN       dbo.Tenants t ON t.TenantId     = u.TenantId
        LEFT JOIN  dbo.IntegrityDecisionResults idr
               ON idr.NominationId = n.NominationId
        WHERE {_INCLUSION_SQL}
          AND u.TenantId = ?
          {window_clause}
        ORDER BY n.NominationDate
    """

    params = [tenant_id] + ([window_days] if window_days is not None else [])
    df = pd.read_sql(query, conn, params=params)
    if (
        "InvalidSyntheticSource" in df.columns
        and pd.to_numeric(df["InvalidSyntheticSource"], errors="coerce")
        .fillna(0)
        .astype(bool)
        .any()
    ):
        raise ValueError(
            f"Tenant {tenant_id} contains SYNTHETIC_GROUND_TRUTH labels but is not "
            "marked is_synthetic; refusing to train."
        )
    df["IsFraud"] = pd.to_numeric(df["IsFraud"], errors="coerce").astype("Int64")
    df["ConfirmedPatterns"] = df.apply(_confirmed_patterns, axis=1)
    return df


_SYNTHETIC_SCENARIO_PATTERN_MAP = {
    "RING": ("RING",),
    "RECIPROCAL": ("RECIPROCAL",),
    "BURST": ("TEMPORAL_BURST",),
}


def _confirmed_patterns(row) -> tuple[str, ...]:
    """Return independently adjudicated v3 behavior labels.

    Older synthetic corpora predate ``confirmed_patterns``.  Their explicit
    scenario family is translated through this deliberately narrow, versioned
    compatibility map.  Broad ``CONCENTRATION``, ``MIXED``, and amount cases
    are not silently relabelled as specialist ground truth.
    """
    raw = row.get("TrainingDispositionMetadataJson")
    metadata = {}
    if isinstance(raw, str) and raw.strip():
        try:
            metadata = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("TrainingDispositionMetadataJson is invalid") from exc
        if not isinstance(metadata, dict):
            raise ValueError("TrainingDispositionMetadataJson must be an object")
    elif isinstance(raw, dict):
        metadata = raw

    values = metadata.get("confirmed_patterns")
    if values is not None:
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise ValueError("confirmed_patterns must be an array of strings")
        return tuple(sorted({
            value.strip().upper() for value in values if value.strip()
        }))

    if row.get("LabelSource") == SOURCE_SYNTHETIC:
        scenario = row.get("ScenarioFamily")
        if isinstance(scenario, str):
            return _SYNTHETIC_SCENARIO_PATTERN_MAP.get(
                scenario.strip().upper(), ()
            )
    return ()


def summarise(df: pd.DataFrame, tenant_id: int) -> dict:
    """
    Per-source counts, for logging and supervised-label evaluation.

    Human and isolated synthetic counts remain separate for provenance, while
    n_supervised is the model-neutral population eligible for model fitting.
    """
    counts = df["LabelSource"].value_counts().to_dict()
    stats = {
        "n_total":      int(len(df)),
        "n_hrbp":       int(counts.get(SOURCE_HRBP, 0)),
        "n_synthetic":  int(counts.get(SOURCE_SYNTHETIC, 0)),
        "n_excluded":   int(counts.get(SOURCE_EXCLUDED, 0)),
        "n_model":      int(counts.get(SOURCE_MODEL, 0)),
        "n_unlabelled": int(counts.get(SOURCE_UNLABELLED, 0)),
        "n_fraud":      int(df["IsFraud"].sum()),
        "n_hrbp_fraud": int(df.loc[df["LabelSource"] == SOURCE_HRBP, "IsFraud"].sum()),
        "n_synthetic_fraud": int(
            df.loc[df["LabelSource"] == SOURCE_SYNTHETIC, "IsFraud"].sum()
        ),
    }
    stats["n_supervised"] = stats["n_hrbp"] + stats["n_synthetic"]
    logger.info(
        "[Tenant %d] labels — total %d | human %d (%d fraud) | synthetic %d "
        "(%d fraud) | excluded %d | model %d | unlabelled %d | fraud %d",
        tenant_id, stats["n_total"], stats["n_hrbp"], stats["n_hrbp_fraud"],
        stats["n_synthetic"], stats["n_synthetic_fraud"],
        stats["n_excluded"], stats["n_model"], stats["n_unlabelled"],
        stats["n_fraud"],
    )
    if stats["n_supervised"] == 0:
        logger.warning(
            "[Tenant %d] NO eligible supervised labels. Model/unlabelled rows "
            "cannot support independent evaluation for this tenant.",
            tenant_id,
        )
    return stats


def human_confirmed(df: pd.DataFrame) -> pd.DataFrame:
    """Return human-reviewed training targets only.

    ``SOURCE_MODEL`` rows are Random Forest outputs, ``SOURCE_UNLABELLED`` rows
    have no outcome evidence, and ``SOURCE_EXCLUDED`` rows were deliberately
    withheld by HRBP. Keeping this filter here preserves a human-only evaluation
    slice even when an isolated synthetic corpus is also available.
    """
    required = {"NominationId", "IsFraud", "LabelSource"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Label frame is missing required columns: {sorted(missing)}")

    confirmed = df.loc[df["LabelSource"] == SOURCE_HRBP].copy()
    if confirmed["IsFraud"].isna().any():
        raise ValueError("Human-confirmed labels must have a non-null IsFraud value")
    confirmed["IsFraud"] = confirmed["IsFraud"].astype(int)
    return confirmed


def supervised_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Return eligible model-neutral human and synthetic training targets.

    Synthetic eligibility is decided in ``load_labels`` only after checking the
    owning tenant's ``is_synthetic`` flag.  This function never treats model
    outputs, RF bootstrap labels, exclusions, or unreviewed rows as targets.
    """
    required = {"NominationId", "IsFraud", "LabelSource"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Label frame is missing required columns: {sorted(missing)}")

    supervised = df.loc[
        df["LabelSource"].isin((SOURCE_HRBP, SOURCE_SYNTHETIC))
    ].copy()
    if supervised["IsFraud"].isna().any():
        raise ValueError("Eligible supervised labels must have a non-null IsFraud value")
    if not supervised["IsFraud"].isin((0, 1)).all():
        raise ValueError("Eligible supervised labels must be binary")
    supervised["IsFraud"] = supervised["IsFraud"].astype(int)
    return supervised


def attach_training_labels(
    feature_df: pd.DataFrame,
    label_df: pd.DataFrame,
) -> pd.DataFrame:
    """Attach the shared label contract to an independently built feature set."""
    columns = [
        "NominationId",
        "IsFraud",
        "LabelSource",
        "TrainingDisposition",
    ]
    missing = set(columns) - set(label_df.columns)
    if missing:
        raise ValueError(f"Label frame is missing required columns: {sorted(missing)}")

    result = feature_df.merge(
        label_df[columns],
        on="NominationId",
        how="left",
        validate="one_to_one",
    )
    if result["LabelSource"].isna().any():
        missing_ids = result.loc[
            result["LabelSource"].isna(), "NominationId"
        ].head(10).tolist()
        raise ValueError(f"Shared label contract missing nominations: {missing_ids}")
    return result
