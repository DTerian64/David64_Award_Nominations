"""Static contract for the schema-migration identity's scoped authority."""

from pathlib import Path


GRANTS_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "soc2-sql-managed-identity"
    / "db-access-grants.sql"
)


def test_migration_role_controls_application_managed_schemas_only():
    source = GRANTS_SCRIPT.read_text(encoding="utf-8")

    assert "CREATE ROLE [award_schema_migrator]" in source
    assert "ALTER ROLE ' + QUOTENAME(@migrationRole)" in source
    for schema in ("dbo", "integrity", "ops"):
        assert (
            f"GRANT CONTROL ON SCHEMA::[{schema}] "
            "TO [award_schema_migrator]"
        ) in source

    assert "ALTER ROLE db_owner" not in source
    assert "GRANT CONTROL SERVER" not in source
