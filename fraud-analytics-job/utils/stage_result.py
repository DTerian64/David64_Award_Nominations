"""Structured result returned by one tenant-scoped analytics stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_STATUSES = {"SUCCEEDED", "SKIPPED"}


@dataclass(frozen=True)
class TenantStageResult:
    status: str
    reason_code: str | None = None
    published_version: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = self.status.upper()
        if normalized not in _STATUSES:
            raise ValueError(f"Unsupported tenant stage status: {self.status}")
        object.__setattr__(self, "status", normalized)

    @classmethod
    def succeeded(
        cls,
        *,
        published_version: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> "TenantStageResult":
        return cls(
            status="SUCCEEDED",
            published_version=published_version,
            diagnostics=diagnostics or {},
        )

    @classmethod
    def skipped(
        cls,
        reason_code: str,
        *,
        diagnostics: dict[str, Any] | None = None,
    ) -> "TenantStageResult":
        return cls(
            status="SKIPPED",
            reason_code=reason_code,
            diagnostics=diagnostics or {},
        )
