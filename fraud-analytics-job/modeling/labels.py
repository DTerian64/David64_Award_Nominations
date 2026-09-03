"""
labels.py — one definition of "fraud" for every model
======================================================

Both the Random Forest and the GNN need a training label. Human outcomes live
in dbo.IntegrityDecisionResults so neither model owns the ground truth. Component
scores remain immutable evidence that can be compared with the HRBP outcome.

    FRAUD      -> IsFraud = 1
    LEGITIMATE -> IsFraud = 0
    EXCLUDED   -> IsFraud = NULL
    NULL       -> IsFraud = NULL

Inference risk and score fields are deliberately absent from this mapping.

    LabelSource   'hrbp'        eligible human ground truth
                  'excluded'    reviewed by HRBP, deliberately not a label
                  'model'       deprecated compatibility value; never emitted
                                by the canonical loader
                  'unlabelled'  no human disposition; IsFraud is NULL

Training behavior
-----------------
Eligible human labels are the only supervised ground truth. Explicitly excluded
reviews and unreviewed rows remain NULL in this shared contract. RF may create
in-memory Isolation Forest pseudo-labels until it has at least 50 human labels
with at least five examples of each class. RF preserves human labels and never
bootstraps explicitly excluded reviews. GNN never consumes these pseudo-labels.

Scope
-----
Bootstrap labels are NOT produced here. bootstrap_fraud_labels() runs an
IsolationForest over P2P_FEATURE_COLUMNS, so it needs the Random Forest's
engineered feature matrix, which this module has no business computing. It stays
in train_rf_model.py as an RF-only cold-start path.

The GNN does not inherit it. Training a GNN on labels produced by an anomaly
detector fitted to the Random Forest's feature space would be a particularly
circular way to violate model independence — so the GNN
requires real labels or skips the tenant, which the sample gate already handles.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# SOURCE_MODEL remains only so parity/audit callers can classify legacy frames;
# load_labels() never emits it.
SOURCE_HRBP       = "hrbp"
SOURCE_EXCLUDED   = "excluded"
SOURCE_MODEL      = "model"
SOURCE_UNLABELLED = "unlabelled"

# Inclusion rules — lifted verbatim from train_rf_model.load_data() so the two
# paths select the same population. Any change here changes the Random Forest.
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
    LabelSource   str   'hrbp' | 'excluded' | 'model' | 'unlabelled'
    RiskLevel     str   the model's own risk level, preserved even where a
                        human has overridden the label
    ConfirmedBy   str   HRBP actor when reviewed, else None
    ConfirmedAt   datetime | None
    TrainingDisposition str | None

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
            CASE
                WHEN idr.TrainingDisposition = 'FRAUD' THEN 1
                WHEN idr.TrainingDisposition = 'LEGITIMATE' THEN 0
                ELSE NULL
            END AS IsFraud,
            CASE
                WHEN idr.TrainingDisposition IN ('FRAUD', 'LEGITIMATE')
                    THEN '{SOURCE_HRBP}'
                WHEN idr.TrainingDisposition = 'EXCLUDED'
                    THEN '{SOURCE_EXCLUDED}'
                ELSE '{SOURCE_UNLABELLED}'
            END AS LabelSource
        FROM       dbo.Nominations n
        JOIN       dbo.Users u   ON u.UserId       = n.NominatorId
        LEFT JOIN  dbo.IntegrityDecisionResults idr
               ON idr.NominationId = n.NominationId
        WHERE {_INCLUSION_SQL}
          AND u.TenantId = ?
          {window_clause}
        ORDER BY n.NominationDate
    """

    params = [tenant_id] + ([window_days] if window_days is not None else [])
    df = pd.read_sql(query, conn, params=params)
    df["IsFraud"] = pd.to_numeric(df["IsFraud"], errors="coerce").astype("Int64")
    return df


def summarise(df: pd.DataFrame, tenant_id: int) -> dict:
    """
    Per-source counts, for logging and human-label evaluation.

    n_hrbp is the number that actually matters: it is the size of the only subset
    on which a GNN-versus-Random-Forest comparison means anything. If it is small,
    no amount of modelling work will make the comparison informative, and that is
    a scheduling fact rather than a modelling one.
    """
    counts = df["LabelSource"].value_counts().to_dict()
    stats = {
        "n_total":      int(len(df)),
        "n_hrbp":       int(counts.get(SOURCE_HRBP, 0)),
        "n_excluded":   int(counts.get(SOURCE_EXCLUDED, 0)),
        "n_model":      int(counts.get(SOURCE_MODEL, 0)),
        "n_unlabelled": int(counts.get(SOURCE_UNLABELLED, 0)),
        "n_fraud":      int(df["IsFraud"].sum()),
        "n_hrbp_fraud": int(df.loc[df["LabelSource"] == SOURCE_HRBP, "IsFraud"].sum()),
    }
    logger.info(
        "[Tenant %d] labels — total %d | hrbp %d (%d fraud) | excluded %d | "
        "model %d | unlabelled %d | fraud %d",
        tenant_id, stats["n_total"], stats["n_hrbp"], stats["n_hrbp_fraud"],
        stats["n_excluded"], stats["n_model"], stats["n_unlabelled"],
        stats["n_fraud"],
    )
    if stats["n_hrbp"] == 0:
        logger.warning(
            "[Tenant %d] NO eligible human-confirmed labels. Model/unlabelled rows "
            "cannot support independent human-label evaluation for this tenant.",
            tenant_id,
        )
    return stats


def human_confirmed(df: pd.DataFrame) -> pd.DataFrame:
    """Return the only rows that are valid independent GNN training targets.

    ``SOURCE_MODEL`` rows are Random Forest outputs, ``SOURCE_UNLABELLED`` rows
    have no outcome evidence, and ``SOURCE_EXCLUDED`` rows were deliberately
    withheld by HRBP. Keeping this filter here prevents any of them from being
    reintroduced into the GNN loss function.
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


def compare_with_legacy(
    labels_df: pd.DataFrame,
    legacy_df: pd.DataFrame,
    tenant_id: int,
) -> dict:
    """
    Read-only parity check against train_rf_model.load_data()'s own IsFraud.

    This is the safety net for the strangler rollout: while the legacy CASE stays
    authoritative, the job runs both and reports divergence. A clean run across
    every tenant for several weeks is the evidence that switching the Random
    Forest over is safe — evidence from the real production label distribution,
    which no fixture can supply.

    Rows where a human has overridden the legacy CASE (LabelSource='hrbp') are
    reported separately as hrbp_overrides and do NOT fail parity — see the module
    docstring. Everything else must be zero.

    legacy_df needs NominationId and IsFraud. Returns a dict; never raises, so a
    problem in the check can never take down the training stage it is watching.
    """
    try:
        merged = labels_df[["NominationId", "IsFraud", "LabelSource"]].merge(
            legacy_df[["NominationId", "IsFraud"]],
            on="NominationId", how="outer", suffixes=("_new", "_legacy"), indicator=True,
        )

        only_new    = merged[merged["_merge"] == "left_only"]
        only_legacy = merged[merged["_merge"] == "right_only"]
        both        = merged[merged["_merge"] == "both"]
        differing = both[
            both["IsFraud_new"].fillna(-1) != both["IsFraud_legacy"].fillna(-1)
        ]

        # A divergence on an hrbp-sourced row is the module working as designed:
        # a human said something the RiskLevel CASE disagrees with. Counting it
        # as a parity failure would mean the safety net goes red exactly when the
        # data gets good, and would be switched off. Only model/unlabelled
        # divergence indicates a genuine defect in this module.
        expected = differing[
            differing["LabelSource"].isin((SOURCE_HRBP, SOURCE_EXCLUDED))
        ]
        unexpected = differing[
            ~differing["LabelSource"].isin((SOURCE_HRBP, SOURCE_EXCLUDED))
        ]

        result = {
            "rows_new":         int(len(labels_df)),
            "rows_legacy":      int(len(legacy_df)),
            "only_in_new":      int(len(only_new)),
            "only_in_legacy":   int(len(only_legacy)),
            "label_mismatch":   int(len(unexpected)),
            "hrbp_overrides":   int(len(expected)),
            "identical":        bool(len(only_new) == 0 and len(only_legacy) == 0
                                     and len(unexpected) == 0),
        }

        if result["identical"]:
            logger.info(
                "[Tenant %d] label parity OK — %d rows, %d human overrides of the "
                "legacy CASE (expected, not a failure).",
                tenant_id, result["rows_new"], result["hrbp_overrides"],
            )
        else:
            logger.error(
                "[Tenant %d] LABEL PARITY FAILED — %d mismatched, %d only in labels.py, "
                "%d only in load_data(). The Random Forest must NOT be switched over "
                "until this is zero.",
                tenant_id, result["label_mismatch"],
                result["only_in_new"], result["only_in_legacy"],
            )
            if len(unexpected):
                by_source = unexpected["LabelSource"].value_counts().to_dict()
                logger.error("[Tenant %d]   mismatches by LabelSource: %s", tenant_id, by_source)
                logger.error(
                    "[Tenant %d]   sample NominationIds: %s",
                    tenant_id, unexpected["NominationId"].head(10).tolist(),
                )
        return result

    except Exception as exc:
        logger.error(
            "[Tenant %d] label parity check itself failed (non-fatal): %s",
            tenant_id, exc, exc_info=True,
        )
        return {"identical": False, "error": str(exc)}
