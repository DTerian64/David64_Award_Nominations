"""
labels.py — one definition of "fraud" for every model (ADR-0002)
================================================================

Both the Random Forest and the GNN need a training label. Today that label is a
CASE expression buried in a 500-line SQL string inside train_fraud_model.load_data():

    IsFraud = CASE WHEN p2p.RiskLevel IN ('HIGH','CRITICAL') THEN 1 ELSE 0 END

That single expression flattens four materially different things into one column:

    a human HRBP decision   arrives as RiskLevel='CRITICAL' via
                            backend upsert_p2p_fraud_label -> indistinguishable
                            from a model prediction
    a model prediction      written by fraud_check.assess() at submission, and
                            rewritten for every historical row each week by
                            score_and_save_historical()
    a cold-start guess      bootstrap_fraud_labels(): IsolationForest at
                            contamination=0.10, plus HIGH/CRITICAL graph findings
    nothing at all          LEFT JOIN with no P2P row -> ELSE 0 -> silently
                            labelled LEGITIMATE

The last one is a real defect: a nomination that never went through the ML
pipeline is not clean, it is unlabelled, and both models are currently being
taught that unexamined nominations are fine.

This module makes those distinguishable. It does NOT change any of them.

    LabelSource   'hrbp'        ConfirmedBy IS NOT NULL — human ground truth
                  'model'       a P2P score row exists; the label is the Random
                                Forest's own prior output
                  'unlabelled'  no P2P score row; IsFraud is 0 by convention,
                                NOT by evidence

Why v1 must reproduce the existing behaviour exactly
-----------------------------------------------------
IsFraud here is byte-for-byte what load_data() produces today, including the
unlabelled -> 0 convention. That is deliberate.

The one intentional exception: when ConfirmedBy IS NOT NULL, the human's IsFraud
value wins over the RiskLevel CASE. Today that exception fires on zero rows —
dbo.P2P_FraudScores has no confirmed labels at all — so parity with load_data()
is still exact. It exists so that a human verdict can be recorded WITHOUT
overwriting the model's score.

That distinction is not cosmetic. backend.utils.sqlhelper2.upsert_p2p_fraud_label
currently slams FraudScore to 100/0 and RiskLevel to CRITICAL/NONE when an HRBP
decides. The moment a human labels a nomination, the model's prediction for that
nomination is destroyed — so the set of rows with ground truth and the set of
rows with a comparable model output are disjoint by construction, and the
ADR-0002 evaluation gate can never be computed. Preserving the score and writing
the verdict alongside it is what makes the gate possible at all.

The rollout for this module is a strangler, not a swap: it runs alongside the
existing CASE, read-only, and the job logs any row-level divergence while the old
path stays authoritative. Only after several weekly runs report zero divergence
across every tenant does the Random Forest switch over. If v1 also "fixed" the
unlabelled rows, the parity check could never be green — so it would be turned
off, and the only real safety net would be gone.

Changing what counts as fraud is a separate, deliberate decision with its own
review. Adding LabelSource is new information about the same decisions.

Scope
-----
Bootstrap labels are NOT produced here. bootstrap_fraud_labels() runs an
IsolationForest over P2P_FEATURE_COLUMNS, so it needs the Random Forest's
engineered feature matrix, which this module has no business computing. It stays
in train_fraud_model.py as an RF-only cold-start path.

The GNN does not inherit it. Training a GNN on labels produced by an anomaly
detector fitted to the Random Forest's feature space would be a particularly
circular way to violate the independence commitment in ADR-0002 — so the GNN
requires real labels or skips the tenant, which the sample gate already handles.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# LabelSource values, in precedence order: a human decision beats a model
# prediction, which beats no evidence at all.
SOURCE_HRBP       = "hrbp"
SOURCE_MODEL      = "model"
SOURCE_UNLABELLED = "unlabelled"

# Inclusion rules — lifted verbatim from train_fraud_model.load_data() so the two
# paths select the same population. Any change here changes the Random Forest.
#
#   PendingHRBPReview                     excluded — no confirmed label yet
#   Rejected by 'Fraud Detection (Description)'
#                                         excluded — Check A description quality
#                                         gate, not a fraud signal
#   Rejected by 'Fraud Detection'         INCLUDED — CRITICAL ML auto-reject.
#                                         LabelSource='model': the RF's own output.
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
    IsFraud       int   0/1 — identical to what load_data() computes today
    LabelSource   str   'hrbp' | 'model' | 'unlabelled'
    RiskLevel     str   the model's own risk level, preserved even where a
                        human has overridden the label
    ConfirmedBy   str   HRBP actor when human-confirmed, else None
    ConfirmedAt   datetime | None

    window_days=None loads the tenant's full history, matching load_data(), which
    has no date filter. The GNN passes a window; the Random Forest does not.

    Requires dbo.P2P_FraudScores.ConfirmedBy — adopted into the migration chain by
    revision 0040. On a database predating that revision this raises rather than
    silently degrading to 'model' for every row, because a silent degrade would
    make the human-label metrics quietly meaningless.
    """
    window_clause = (
        "AND n.NominationDate >= DATEADD(DAY, -?, GETDATE())"
        if window_days is not None else ""
    )

    query = f"""
        SELECT
            n.NominationId,
            p2p.RiskLevel,
            p2p.ConfirmedBy,
            p2p.ConfirmedAt,
            CASE
                -- A human verdict is authoritative and does NOT overwrite the
                -- model's score, so model-vs-human agreement stays measurable.
                WHEN p2p.ConfirmedBy IS NOT NULL AND p2p.IsFraud IS NOT NULL
                    THEN p2p.IsFraud
                WHEN p2p.RiskLevel IN ('HIGH', 'CRITICAL') THEN 1
                ELSE 0
            END AS IsFraud,
            CASE
                WHEN p2p.ConfirmedBy IS NOT NULL THEN '{SOURCE_HRBP}'
                WHEN p2p.NominationId IS NOT NULL THEN '{SOURCE_MODEL}'
                ELSE '{SOURCE_UNLABELLED}'
            END AS LabelSource
        FROM       dbo.Nominations n
        JOIN       dbo.Users u   ON u.UserId       = n.NominatorId
        LEFT JOIN  dbo.P2P_FraudScores p2p ON p2p.NominationId = n.NominationId
        WHERE {_INCLUSION_SQL}
          AND u.TenantId = ?
          {window_clause}
        ORDER BY n.NominationDate
    """

    params = [tenant_id] + ([window_days] if window_days is not None else [])
    df = pd.read_sql(query, conn, params=params)
    df["IsFraud"] = df["IsFraud"].astype(int)
    return df


def summarise(df: pd.DataFrame, tenant_id: int) -> dict:
    """
    Per-source counts, for logging and for the ADR-0002 evaluation gate.

    n_hrbp is the number that actually matters: it is the size of the only subset
    on which a GNN-versus-Random-Forest comparison means anything. If it is small,
    no amount of modelling work will make the comparison informative, and that is
    a scheduling fact rather than a modelling one.
    """
    counts = df["LabelSource"].value_counts().to_dict()
    stats = {
        "n_total":      int(len(df)),
        "n_hrbp":       int(counts.get(SOURCE_HRBP, 0)),
        "n_model":      int(counts.get(SOURCE_MODEL, 0)),
        "n_unlabelled": int(counts.get(SOURCE_UNLABELLED, 0)),
        "n_fraud":      int(df["IsFraud"].sum()),
        "n_hrbp_fraud": int(df.loc[df["LabelSource"] == SOURCE_HRBP, "IsFraud"].sum()),
    }
    logger.info(
        "[Tenant %d] labels — total %d | hrbp %d (%d fraud) | model %d | unlabelled %d | fraud %d",
        tenant_id, stats["n_total"], stats["n_hrbp"], stats["n_hrbp_fraud"],
        stats["n_model"], stats["n_unlabelled"], stats["n_fraud"],
    )
    if stats["n_hrbp"] == 0:
        logger.warning(
            "[Tenant %d] NO human-confirmed labels. Every label is the Random Forest's "
            "own prior output, so reported model quality is self-referential and the "
            "ADR-0002 evaluation gate cannot be evaluated for this tenant.",
            tenant_id,
        )
    return stats


def compare_with_legacy(
    labels_df: pd.DataFrame,
    legacy_df: pd.DataFrame,
    tenant_id: int,
) -> dict:
    """
    Read-only parity check against train_fraud_model.load_data()'s own IsFraud.

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
        differing   = both[both["IsFraud_new"] != both["IsFraud_legacy"]]

        # A divergence on an hrbp-sourced row is the module working as designed:
        # a human said something the RiskLevel CASE disagrees with. Counting it
        # as a parity failure would mean the safety net goes red exactly when the
        # data gets good, and would be switched off. Only model/unlabelled
        # divergence indicates a genuine defect in this module.
        expected    = differing[differing["LabelSource"] == SOURCE_HRBP]
        unexpected  = differing[differing["LabelSource"] != SOURCE_HRBP]

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
