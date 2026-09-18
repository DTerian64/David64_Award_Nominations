"""Non-serving T2 builder for the Award Nomination Tabular-v1 contract.

Random Forest and ``tabular_mlp`` consume the same ordered feature information.
This first implementation intentionally reproduces the deployed RF transform
while the old and new paths run in parity. The production trainer is not
switched to this builder in T2. Once live parity is accepted, the compatibility
entry point can delegate here and the duplicate legacy functions can be removed.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity as sk_cosine_similarity

from feature_builders.contracts import TabularFeatureDataset, TabularFeatureSchema
from integrity_data import IntegrityDataset, validate_dataset
from source_adapters.contracts import SourceCapability


TABULAR_V1_FEATURE_COLUMNS = (
    "Amount",
    "DayOfWeekSin",
    "DayOfWeekCos",
    "MonthSin",
    "MonthCos",
    "IsWeekend",
    "NominatorTotalNominations",
    "NominatorAvgAmount",
    "NominatorStdAmount",
    "NominatorUniqueBeneficiaries",
    "BeneficiaryTotalReceived",
    "BeneficiaryAvgAmountReceived",
    "HasReciprocalNomination",
    "PairNominationCount",
    "AmountZScore",
    "IsHighAmount",
    "NominatorConcentrationRatio",
    "CategoryFraudRate",
    "DescriptionCosineSim",
    "DescriptionEmbDistance",
    "TransactionalPhraseScore",
)

AWARD_NOMINATION_TABULAR_V1_SCHEMA = TabularFeatureSchema(
    name="award-nomination-tabular",
    version="tabular-v1",
    source_system="AWARD_NOMINATION",
    required_capabilities=frozenset(
        {
            SourceCapability.DIRECTED_ACTOR_PAIR.value,
            SourceCapability.AMOUNT.value,
            SourceCapability.CATEGORY.value,
            SourceCapability.TEXT.value,
            SourceCapability.EVENT_STATUS.value,
            SourceCapability.REVIEWED_OUTCOMES.value,
        }
    ),
    feature_columns=TABULAR_V1_FEATURE_COLUMNS,
)

_TRANSACTIONAL_PHRASE_REFERENCE_HITS = 6.0
_TRANSACTIONAL_PHRASE_PATTERN = re.compile(
    r"\b(?:"
    r"helped me|help me|"
    r"my deadline|our deadline|"
    r"saved my|saved the day|"
    r"owe[sd]? (?:him|her|them|me)|"
    r"in return|return the favor|"
    r"scratch my back|you scratch|"
    r"promised|will nominate|going to nominate|"
    r"nominate (?:you|him|her|them) (?:next|back|in return)|"
    r"my project|my task|my work"
    r")\b",
    re.IGNORECASE,
)

_HUMAN_PROVENANCE = frozenset({"HUMAN_INVESTIGATION", "RANDOM_AUDIT"})


def transactional_phrase_score(description: str | None) -> float:
    hits = sum(1 for _ in _TRANSACTIONAL_PHRASE_PATTERN.finditer(description or ""))
    return round(min(hits / _TRANSACTIONAL_PHRASE_REFERENCE_HITS, 1.0), 4)


def _category_id(value: str | None) -> int | str | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _label_values(label: Any | None) -> tuple[Any, str, str | None]:
    if label is None:
        return pd.NA, "unlabelled", None
    if label.disposition == "EXCLUDED":
        return pd.NA, "excluded", label.disposition
    if label.disposition not in {"FRAUD", "LEGITIMATE"}:
        return pd.NA, "unlabelled", label.disposition
    is_fraud = 1 if label.disposition == "FRAUD" else 0
    if label.provenance in _HUMAN_PROVENANCE:
        return is_fraud, "hrbp", label.disposition
    if label.provenance == "SYNTHETIC_GROUND_TRUTH":
        return is_fraud, "synthetic_ground_truth", label.disposition
    return pd.NA, "unlabelled", label.disposition


def _eligible_for_rf(event: Any) -> bool:
    if (event.status or "") == "PendingHRBPReview":
        return False
    return not (
        event.status == "Rejected"
        and event.attributes.get("rejection_actor")
        == "Fraud Detection (Description)"
    )


def build_nomination_frame(dataset: IntegrityDataset) -> pd.DataFrame:
    """Reconstruct the current RF input population from canonical records."""

    validate_dataset(dataset)
    schema = AWARD_NOMINATION_TABULAR_V1_SCHEMA
    if dataset.snapshot.source_system != schema.source_system:
        raise ValueError(
            f"{schema.schema_id} cannot consume source "
            f"{dataset.snapshot.source_system!r}"
        )
    missing_capabilities = schema.required_capabilities - dataset.snapshot.capabilities
    if missing_capabilities:
        raise ValueError(
            f"{schema.schema_id} is missing source capabilities: "
            f"{sorted(missing_capabilities)}"
        )

    roles: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for participant in dataset.participants:
        roles[participant.event_id][participant.source_role].append(
            participant.actor_id
        )
    labels = {label.event_id: label for label in dataset.labels}

    rows: list[dict[str, Any]] = []
    ordered_events = sorted(
        dataset.events,
        key=lambda item: (item.occurred_at, int(item.event_id)),
    )
    for event in ordered_events:
        if not _eligible_for_rf(event):
            continue
        event_roles = roles[event.event_id]
        nominators = event_roles.get("NOMINATOR", [])
        beneficiaries = event_roles.get("BENEFICIARY", [])
        if len(nominators) != 1 or len(beneficiaries) != 1:
            raise ValueError(
                f"Nomination {event.event_id} requires exactly one NOMINATOR and "
                "one BENEFICIARY"
            )
        is_fraud, label_source, disposition = _label_values(
            labels.get(event.event_id)
        )
        rows.append(
            {
                "NominationId": int(event.event_id),
                "NominatorId": int(nominators[0]),
                "BeneficiaryId": int(beneficiaries[0]),
                "Amount": float(event.amount) if event.amount is not None else np.nan,
                "Currency": event.currency,
                "NominationDescription": event.text,
                "NominationDate": event.occurred_at.replace(tzinfo=None),
                "Status": event.status,
                "RejectionActor": event.attributes.get("rejection_actor"),
                "CategoryId": _category_id(event.category),
                "IsFraud": is_fraud,
                "LabelSource": label_source,
                "TrainingDisposition": disposition,
            }
        )

    columns = [
        "NominationId",
        "NominatorId",
        "BeneficiaryId",
        "Amount",
        "Currency",
        "NominationDescription",
        "NominationDate",
        "Status",
        "RejectionActor",
        "CategoryId",
        "IsFraud",
        "LabelSource",
        "TrainingDisposition",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame["IsFraud"] = frame["IsFraud"].astype("Int64")
    return frame


def add_semantic_features(df: pd.DataFrame, embed_model: Any) -> pd.DataFrame:
    """Reproduce the deployed RF semantic transform for parity testing."""

    descriptions = df["NominationDescription"].fillna("").tolist()
    if not any(descriptions):
        df["DescriptionCosineSim"] = 0.0
        df["DescriptionEmbDistance"] = 1.0
        return df

    all_embs = embed_model.encode(
        descriptions,
        batch_size=64,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    beneficiary_embeddings: dict[Any, Any] = {}
    for user_id, group in df.groupby("BeneficiaryId"):
        indices = group.index.tolist()
        user_embeddings = all_embs[df.index.get_indexer(indices)]
        if len(user_embeddings) > 0:
            beneficiary_embeddings[user_id] = user_embeddings.mean(axis=0)

    cosine_sims: list[float] = []
    embedding_distances: list[float] = []
    for position, (_, row) in enumerate(df.iterrows()):
        nomination_embedding = all_embs[position]
        beneficiary_mean = beneficiary_embeddings.get(row["BeneficiaryId"])
        if beneficiary_mean is None:
            similarity = 0.0
            distance = 1.0
        else:
            similarity = float(
                sk_cosine_similarity(
                    [nomination_embedding], [beneficiary_mean]
                )[0][0]
            )
            distance = float(np.linalg.norm(nomination_embedding - beneficiary_mean))
        cosine_sims.append(similarity)
        embedding_distances.append(distance)

    df["DescriptionCosineSim"] = cosine_sims
    df["DescriptionEmbDistance"] = embedding_distances
    return df


def extract_features(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[Any, float], float]:
    """Reproduce the deployed RF deterministic feature transform."""

    df["TransactionalPhraseScore"] = (
        df["NominationDescription"].fillna("").map(transactional_phrase_score)
    )
    df["NominationDate"] = pd.to_datetime(df["NominationDate"])
    df["DayOfWeek"] = df["NominationDate"].dt.dayofweek
    df["Month"] = df["NominationDate"].dt.month
    df["Hour"] = df["NominationDate"].dt.hour
    df["IsWeekend"] = df["DayOfWeek"].isin([5, 6]).astype(int)
    # Tabular-v1 exposes cyclic coordinates to both candidate architectures.
    # Retain the raw values in the audit frame for RF-v3 parity and diagnostics.
    df["DayOfWeekSin"] = np.sin(2 * np.pi * df["DayOfWeek"] / 7)
    df["DayOfWeekCos"] = np.cos(2 * np.pi * df["DayOfWeek"] / 7)
    zero_based_month = df["Month"] - 1
    df["MonthSin"] = np.sin(2 * np.pi * zero_based_month / 12)
    df["MonthCos"] = np.cos(2 * np.pi * zero_based_month / 12)

    nominator_stats = df.groupby("NominatorId").agg(
        NominatorTotalNominations=("NominationId", "count"),
        NominatorAvgAmount=("Amount", "mean"),
        NominatorStdAmount=("Amount", "std"),
        NominatorMinAmount=("Amount", "min"),
        NominatorMaxAmount=("Amount", "max"),
        NominatorUniqueBeneficiaries=("BeneficiaryId", "nunique"),
    ).reset_index()
    df = df.merge(nominator_stats, on="NominatorId", how="left")

    beneficiary_stats = df.groupby("BeneficiaryId").agg(
        BeneficiaryTotalReceived=("NominationId", "count"),
        BeneficiaryAvgAmountReceived=("Amount", "mean"),
    ).reset_index()
    df = df.merge(beneficiary_stats, on="BeneficiaryId", how="left")

    reciprocal = df.merge(
        df[["NominatorId", "BeneficiaryId"]],
        left_on=["NominatorId", "BeneficiaryId"],
        right_on=["BeneficiaryId", "NominatorId"],
        how="inner",
        suffixes=("", "_reciprocal"),
    )
    df["HasReciprocalNomination"] = (
        df["NominationId"].isin(reciprocal["NominationId"]).astype(int)
    )
    pair_counts = (
        df.groupby(["NominatorId", "BeneficiaryId"])
        .size()
        .reset_index(name="PairNominationCount")
    )
    df = df.merge(pair_counts, on=["NominatorId", "BeneficiaryId"], how="left")

    amount_mean = df["Amount"].mean()
    amount_std = df["Amount"].std()
    df["AmountZScore"] = (
        (df["Amount"] - amount_mean) / amount_std
        if amount_std and amount_std > 0
        else 0.0
    )
    df["IsHighAmount"] = (df["AmountZScore"] > 2).astype(int)
    df["IsLowAmount"] = (df["AmountZScore"] < -2).astype(int)
    df["NominatorConcentrationRatio"] = df["NominatorTotalNominations"] / (
        df["NominatorUniqueBeneficiaries"] + 1
    )

    if "CategoryId" in df.columns and "IsFraud" in df.columns:
        observed_rate = df["IsFraud"].mean()
        global_fraud_rate = 0.0 if pd.isna(observed_rate) else float(observed_rate)
        category_fraud_rate = (
            df.groupby("CategoryId")["IsFraud"]
            .mean()
            .fillna(0.0)
            .astype(float)
            .to_dict()
        )
        df["CategoryFraudRate"] = (
            df["CategoryId"].map(category_fraud_rate).fillna(0.0)
        )
    else:
        df["CategoryFraudRate"] = 0.0
        category_fraud_rate = {}
        global_fraud_rate = 0.0
    return df, category_fraud_rate, global_fraud_rate


class AwardNominationTabularV1FeatureBuilder:
    """Build the shared Tabular-v1 matrix from a canonical source snapshot."""

    schema = AWARD_NOMINATION_TABULAR_V1_SCHEMA

    def build(
        self,
        dataset: IntegrityDataset,
        *,
        embed_model: Any,
    ) -> TabularFeatureDataset:
        frame = build_nomination_frame(dataset)
        frame = add_semantic_features(frame, embed_model)
        frame, category_rates, global_rate = extract_features(frame)
        result = TabularFeatureDataset(
            schema=self.schema,
            source_snapshot_id=dataset.snapshot.snapshot_id,
            frame=frame,
            features=frame.loc[:, list(self.schema.feature_columns)].copy(),
            target=frame[self.schema.target_column].copy(),
            fitted_state={
                "category_fraud_rate": category_rates,
                "global_fraud_rate": global_rate,
            },
            diagnostics={
                "source_event_count": len(dataset.events),
                "eligible_event_count": len(frame),
                "excluded_by_rf_policy": len(dataset.events) - len(frame),
            },
        )
        result.validate()
        return result
