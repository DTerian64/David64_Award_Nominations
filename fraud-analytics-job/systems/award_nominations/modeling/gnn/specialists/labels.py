"""Build independent binary populations for each v3 behavior track."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from .contracts import BEHAVIOR_TRACKS


def _patterns(value: object) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        raise ValueError("ConfirmedPatterns must not be a scalar string")
    if not isinstance(value, Iterable):
        raise ValueError("ConfirmedPatterns must be iterable")
    return frozenset(
        str(item).strip().upper() for item in value if str(item).strip()
    )


def build_specialist_label_maps(
    labelled: pd.DataFrame,
) -> dict[str, dict[int, int]]:
    """Build each track without turning other fraud patterns into negatives."""
    required = {"NominationId", "IsFraud", "ConfirmedPatterns"}
    missing = required - set(labelled.columns)
    if missing:
        raise ValueError(
            f"Specialist label frame is missing columns: {sorted(missing)}"
        )

    maps = {track: {} for track in BEHAVIOR_TRACKS}
    for row in labelled.itertuples(index=False):
        nomination_id = int(row.NominationId)
        disposition = int(row.IsFraud)
        if disposition == 0:
            for label_map in maps.values():
                label_map[nomination_id] = 0
            continue
        if disposition != 1:
            raise ValueError("Specialist dispositions must be binary")
        confirmed = _patterns(row.ConfirmedPatterns)
        for track in confirmed & set(BEHAVIOR_TRACKS):
            maps[track][nomination_id] = 1
    return maps
