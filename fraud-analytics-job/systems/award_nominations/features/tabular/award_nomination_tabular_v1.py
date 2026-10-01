"""Shared RF/Tabular MLP features with causal, tenant-configured history."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd
from integrity_engine.tabular_history import HISTORY_CONTRACT, TabularHistory, semantic_features

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
    version="tabular-v2",
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
                "LabelKnownAt": labels[event.event_id].known_at if event.event_id in labels else None,
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
        "LabelKnownAt",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame["IsFraud"] = frame["IsFraud"].astype("Int64")
    return frame


def extract_features(df: pd.DataFrame, window_days: int = 365) -> tuple[pd.DataFrame, dict[Any, float], float]:
    """Build bounded prior-only numeric features; category encoding is fitted
    separately on each training partition, never on the full label population.
    """
    history = TabularHistory(window_days)
    df = df.copy()
    df["NominationDate"] = pd.to_datetime(df["NominationDate"])
    values = {}
    for index, row in df.sort_values(["NominationDate", "NominationId"]).iterrows():
        target = row.to_dict()
        values[index] = history.features(target)
        history.add(target)
    for column in (next(iter(values.values())).keys() if values else TABULAR_V1_FEATURE_COLUMNS):
        if column not in {"DescriptionCosineSim", "DescriptionEmbDistance"}:
            df[column] = pd.Series({index: item[column] for index, item in values.items()}, dtype=float)
    df["TransactionalPhraseScore"] = df["NominationDescription"].fillna("").map(transactional_phrase_score)
    df["CategoryFraudRate"] = 0.0
    return df, {}, 0.0


def add_semantic_features(df: pd.DataFrame, embed_model: Any, window_days: int = 365) -> pd.DataFrame:
    """Compare with the beneficiary's latest 20 prior authored descriptions."""
    df = df.copy()
    df["DescriptionCosineSim"] = 0.0
    df["DescriptionEmbDistance"] = 1.0
    if df.empty:
        return df
    descriptions = df["NominationDescription"].fillna("").tolist()
    vectors = embed_model.encode(descriptions, batch_size=64, show_progress_bar=False, normalize_embeddings=True) if any(descriptions) else None
    indexed_vectors = dict(zip(df.index, vectors)) if vectors is not None else {}
    history = TabularHistory(window_days)
    for index, row in df.sort_values(["NominationDate", "NominationId"]).iterrows():
        target = row.to_dict()
        target["NominationDescription"] = target.get("NominationDescription") or ""
        history.expire(target["NominationDate"])
        prior = history.prior_descriptions(target["BeneficiaryId"])
        if target["NominationDescription"].strip() and prior and vectors is not None:
            similarity, distance = semantic_features(indexed_vectors[index], [item["_embedding"] for item in prior])
            df.loc[index, ["DescriptionCosineSim", "DescriptionEmbDistance"]] = similarity, distance
        target["_embedding"] = indexed_vectors.get(index)
        history.add(target)
    return df


class AwardNominationTabularV1FeatureBuilder:
    """Shared builder; the persisted schema is v2 for causal-window semantics.

    The Python entry-point name is retained for source-adapter compatibility.
    """
    schema = AWARD_NOMINATION_TABULAR_V1_SCHEMA

    def build(self, dataset: IntegrityDataset, *, embed_model: Any, window_days: int = 365) -> TabularFeatureDataset:
        frame = build_nomination_frame(dataset)
        eligible_count = len(frame)
        frame = add_semantic_features(frame, embed_model, window_days)
        frame, category_rates, global_rate = extract_features(frame, window_days)
        target_start = dataset.snapshot.as_of_exclusive.replace(tzinfo=None) - pd.Timedelta(days=window_days)
        frame = frame.loc[frame["NominationDate"] >= target_start].copy()
        result = TabularFeatureDataset(
            schema=self.schema,
            source_snapshot_id=dataset.snapshot.snapshot_id,
            frame=frame,
            features=frame.loc[:, list(self.schema.feature_columns)].copy(),
            target=frame[self.schema.target_column].copy(),
            fitted_state={"category_fraud_rate": category_rates, "global_fraud_rate": global_rate,
                          "history_window_days": window_days, "history_feature_contract": HISTORY_CONTRACT},
            diagnostics={"source_event_count": len(dataset.events), "eligible_event_count": len(frame),
                         "history_context_event_count": eligible_count - len(frame),
                         "excluded_by_rf_policy": len(dataset.events) - eligible_count,
                         "window_days": window_days},
        )
        result.validate()
        return result
