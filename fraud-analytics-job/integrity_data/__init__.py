"""Source-neutral records consumed by integrity feature builders."""

from .contracts import (
    Actor,
    DatasetSnapshot,
    EventParticipant,
    IntegrityDataset,
    IntegrityEvent,
    OutcomeLabel,
    Relationship,
    as_utc,
)
from .snapshot import build_snapshot, compute_records_sha256
from .validation import DatasetValidationError, validate_dataset

__all__ = [
    "Actor",
    "DatasetSnapshot",
    "DatasetValidationError",
    "EventParticipant",
    "IntegrityDataset",
    "IntegrityEvent",
    "OutcomeLabel",
    "Relationship",
    "as_utc",
    "build_snapshot",
    "compute_records_sha256",
    "validate_dataset",
]
