"""Shared, JSON-safe contracts for GNN evaluators."""

from __future__ import annotations

from typing import Any, TypedDict


class CandidateSpec(TypedDict):
    """One diagnostic candidate and the feature profile it consumes."""

    architecture: str
    feature_profile: str


JsonObject = dict[str, Any]

