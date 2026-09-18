"""Leakage-safe temporal splits shared by Tabular candidate trainers."""

from __future__ import annotations

import math

import pandas as pd

from feature_builders import TabularFeatureDataset

from .contracts import TabularTrainingPolicy, TemporalHoldout

SUPERVISED_LABEL_SOURCES = frozenset({"hrbp", "synthetic_ground_truth"})


def supervised_temporal_holdout(
    dataset: TabularFeatureDataset,
    policy: TabularTrainingPolicy,
) -> tuple[pd.DataFrame, TemporalHoldout | None, str | None]:
    """Return eligible rows and an out-of-time holdout, or a reason to skip."""

    dataset.validate()
    required = {"NominationId", "NominationDate", "LabelSource"}
    missing = required - set(dataset.frame.columns)
    if missing:
        raise ValueError(f"Tabular audit frame is missing: {sorted(missing)}")

    eligible_mask = (
        dataset.frame["LabelSource"].isin(SUPERVISED_LABEL_SOURCES)
        & dataset.target.notna()
    )
    eligible = dataset.frame.loc[eligible_mask].copy()
    eligible[dataset.schema.target_column] = dataset.target.loc[eligible.index].astype(int)
    eligible["NominationDate"] = pd.to_datetime(eligible["NominationDate"])
    eligible = eligible.sort_values(["NominationDate", "NominationId"])

    minimum_total = policy.minimum_training_samples + policy.minimum_evaluation_samples
    if len(eligible) < minimum_total:
        return eligible, None, (
            f"{len(eligible)} supervised rows; requires at least {minimum_total}"
        )

    evaluation_count = max(
        policy.minimum_evaluation_samples,
        int(math.ceil(len(eligible) * policy.evaluation_fraction)),
    )
    training_count = len(eligible) - evaluation_count
    if training_count < policy.minimum_training_samples:
        return eligible, None, (
            f"temporal split leaves {training_count} training rows; requires "
            f"{policy.minimum_training_samples}"
        )

    train = eligible.iloc[:training_count]
    evaluation = eligible.iloc[training_count:]
    target_column = dataset.schema.target_column
    for split_name, split in (("train", train), ("evaluation", evaluation)):
        counts = split[target_column].value_counts().to_dict()
        minority_count = min(int(counts.get(0, 0)), int(counts.get(1, 0)))
        if minority_count < policy.minimum_class_samples_per_split:
            return eligible, None, (
                f"{split_name} split has {counts.get(1, 0)} fraud and "
                f"{counts.get(0, 0)} legitimate rows; requires at least "
                f"{policy.minimum_class_samples_per_split} of each class"
            )

    holdout = TemporalHoldout(
        train_index=tuple(train.index),
        evaluation_index=tuple(evaluation.index),
        train_nomination_ids=tuple(train["NominationId"].astype(int)),
        evaluation_nomination_ids=tuple(evaluation["NominationId"].astype(int)),
        train_end=train["NominationDate"].max().isoformat(),
        evaluation_start=evaluation["NominationDate"].min().isoformat(),
    )
    return eligible, holdout, None
