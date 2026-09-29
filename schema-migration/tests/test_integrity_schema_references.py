"""Prevent active code from reintroducing dbo references to integrity tables."""

from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_ROOTS = (
    "auxiliary-service",
    "backend",
    "fraud-analytics-job",
    "integrity-check",
    "integrity-check-extension",
    "scripts",
    "terraform",
)
SEARCH_SUFFIXES = {".md", ".py", ".sql", ".tf", ".yaml", ".yml"}
IGNORED_PARTS = {".git", ".pytest_cache", ".ruff_cache", "__pycache__"}

TABLES = (
    "GNN_UserEmbeddings",
    "GNNScoringPolicies",
    "GraphPatternFindings",
    "GraphScoringChangeRequests",
    "GraphScoringPatternParameters",
    "GraphScoringPolicies",
    "IntegrityComponentStatus_History",
    "IntegrityComponentStatus",
    "IntegrityDecisionResults",
    "NomGraph_NominationEmbedding",
    "NomGraph_Nominated",
    "NomGraph_Person",
    "UserGraphFlags",
    "ApproverPairFlags",
)

_FORBIDDEN = re.compile(
    rf"(?:\bdbo\.|\[dbo\]\.\[?)(?:{'|'.join(map(re.escape, TABLES))})\]?\b",
    re.IGNORECASE,
)


def test_active_code_has_no_dbo_qualified_integrity_tables():
    violations: list[str] = []

    for relative_root in ACTIVE_ROOTS:
        for path in (REPOSITORY_ROOT / relative_root).rglob("*"):
            if (
                not path.is_file()
                or path.suffix.lower() not in SEARCH_SUFFIXES
                or any(part in IGNORED_PARTS for part in path.parts)
            ):
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _FORBIDDEN.search(line):
                    violations.append(
                        f"{path.relative_to(REPOSITORY_ROOT)}:{line_number}: {line.strip()}"
                    )

    assert not violations, (
        "Active code must use the integrity schema for migrated tables:\n"
        + "\n".join(violations)
    )
