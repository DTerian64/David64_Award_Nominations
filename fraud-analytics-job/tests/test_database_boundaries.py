"""Guard the source/Sentinel database ownership boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python_sources(folder: Path):
    return (path for path in folder.rglob("*.py") if "tests" not in path.parts)


def test_award_dbo_references_are_owned_by_award_system():
    violations = []
    allowed = ROOT / "systems" / "award_nominations"
    for path in _python_sources(ROOT):
        if allowed in path.parents:
            continue
        if "dbo." in path.read_text(encoding="utf-8"):
            violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []


def test_integrity_and_ops_sql_are_owned_by_integrity_sentinel():
    violations = []
    allowed = ROOT / "integrity_sentinel"
    for path in _python_sources(ROOT):
        if allowed in path.parents:
            continue
        source = path.read_text(encoding="utf-8")
        # Documentation may name the stores; SQL-bearing modules contain the
        # schema name next to a SQL operation or FROM/JOIN clause.
        sql_markers = (
            "FROM integrity.",
            "JOIN integrity.",
            "INTO integrity.",
            "UPDATE integrity.",
            "DELETE FROM integrity.",
            "FROM ops.",
            "JOIN ops.",
            "INTO ops.",
            "UPDATE ops.",
        )
        if any(marker in source for marker in sql_markers):
            violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []


def test_legacy_unqualified_sql_environment_names_are_not_used():
    violations = []
    for path in _python_sources(ROOT):
        if path.name == "db_conn.py":
            continue
        source = path.read_text(encoding="utf-8")
        if '"SQL_SERVER"' in source or '"SQL_DATABASE"' in source:
            violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []
