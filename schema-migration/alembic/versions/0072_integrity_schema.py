"""Move integrity model tables from dbo into the integrity schema.

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-29

This is the database half of a coordinated sandbox cutover. Application
revisions that address these objects as ``integrity.<table>`` must remain
stopped until this migration commits.

The migration deliberately rejects direct object permissions and database
modules that reference a target by its old schema-qualified name. ALTER SCHEMA
does not preserve the former or rewrite the latter, so silently proceeding
would produce a partially functional database.
"""

from __future__ import annotations

from alembic import op


revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


_CURRENT_TABLE = "IntegrityComponentStatus"
_HISTORY_TABLE = "IntegrityComponentStatus_History"

_TABLES = (
    "GNN_UserEmbeddings",
    "GNNScoringPolicies",
    "GraphPatternFindings",
    "GraphScoringChangeRequests",
    "GraphScoringPatternParameters",
    "GraphScoringPolicies",
    _CURRENT_TABLE,
    _HISTORY_TABLE,
    "IntegrityDecisionResults",
    "NomGraph_NominationEmbedding",
    "NomGraph_Nominated",
    "NomGraph_Person",
    "UserGraphFlags",
    "ApproverPairFlags",
)

_NON_TEMPORAL_TABLES = tuple(
    table for table in _TABLES if table not in {_CURRENT_TABLE, _HISTORY_TABLE}
)


def _values_sql() -> str:
    return ",\n                ".join(f"(N'{table}')" for table in _TABLES)


def _preflight_sql(source_schema: str, destination_schema: str) -> str:
    values = _values_sql()
    return f"""
        DECLARE @Targets TABLE (TableName sysname NOT NULL PRIMARY KEY);
        INSERT INTO @Targets (TableName)
        VALUES {values};

        IF EXISTS (
            SELECT 1
            FROM @Targets AS target
            WHERE OBJECT_ID(
                QUOTENAME(N'{source_schema}') + N'.' + QUOTENAME(target.TableName),
                N'U'
            ) IS NULL
        )
        BEGIN
            ;THROW 51000,
                'Integrity schema migration preflight failed: a source table is missing.',
                1;
        END;

        IF EXISTS (
            SELECT 1
            FROM @Targets AS target
            WHERE OBJECT_ID(
                QUOTENAME(N'{destination_schema}') + N'.' + QUOTENAME(target.TableName),
                N'U'
            ) IS NOT NULL
        )
        BEGIN
            ;THROW 51001,
                'Integrity schema migration preflight failed: a destination table already exists.',
                1;
        END;

        IF NOT EXISTS (
            SELECT 1
            FROM sys.tables AS current_table
            JOIN sys.tables AS history_table
              ON history_table.object_id = current_table.history_table_id
            WHERE current_table.object_id = OBJECT_ID(
                      N'{source_schema}.{_CURRENT_TABLE}', N'U'
                  )
              AND current_table.temporal_type = 2
              AND history_table.object_id = OBJECT_ID(
                      N'{source_schema}.{_HISTORY_TABLE}', N'U'
                  )
        )
        BEGIN
            ;THROW 51002,
                'Integrity schema migration preflight failed: the temporal current/history relationship is unexpected.',
                1;
        END;

        IF EXISTS (
            SELECT 1
            FROM sys.database_permissions AS permission
            JOIN @Targets AS target
              ON permission.major_id = OBJECT_ID(
                     QUOTENAME(N'{source_schema}') + N'.' + QUOTENAME(target.TableName),
                     N'U'
                 )
            WHERE permission.class = 1
        )
        BEGIN
            ;THROW 51003,
                'Integrity schema migration preflight failed: direct object permissions must be reviewed before transfer.',
                1;
        END;

        IF EXISTS (
            SELECT 1
            FROM sys.sql_expression_dependencies AS dependency
            JOIN sys.objects AS referencing_object
              ON referencing_object.object_id = dependency.referencing_id
            JOIN @Targets AS target
              ON dependency.referenced_id = OBJECT_ID(
                     QUOTENAME(N'{source_schema}') + N'.' + QUOTENAME(target.TableName),
                     N'U'
                 )
            WHERE referencing_object.type IN (
                N'P', N'V', N'FN', N'IF', N'TF', N'TR'
            )
        )
        BEGIN
            ;THROW 51004,
                'Integrity schema migration preflight failed: a database module references a target table.',
                1;
        END;

        IF HAS_PERMS_BY_NAME(
               N'{source_schema}', N'SCHEMA', N'CONTROL'
           ) <> 1
           OR HAS_PERMS_BY_NAME(
               N'{destination_schema}', N'SCHEMA', N'CONTROL'
           ) <> 1
        BEGIN
            ;THROW 51005,
                'Integrity schema migration requires CONTROL on both source and destination schemas.',
                1;
        END;
    """


def _validate_sql(schema: str, previous_schema: str) -> str:
    values = _values_sql()
    return f"""
        DECLARE @Targets TABLE (TableName sysname NOT NULL PRIMARY KEY);
        INSERT INTO @Targets (TableName)
        VALUES {values};

        IF EXISTS (
            SELECT 1
            FROM @Targets AS target
            WHERE OBJECT_ID(
                QUOTENAME(N'{schema}') + N'.' + QUOTENAME(target.TableName),
                N'U'
            ) IS NULL
               OR OBJECT_ID(
                QUOTENAME(N'{previous_schema}') + N'.' + QUOTENAME(target.TableName),
                N'U'
            ) IS NOT NULL
        )
        BEGIN
            ;THROW 51010,
                'Integrity schema migration validation failed: object location is inconsistent.',
                1;
        END;

        IF NOT EXISTS (
            SELECT 1
            FROM sys.tables AS current_table
            JOIN sys.tables AS history_table
              ON history_table.object_id = current_table.history_table_id
            JOIN sys.schemas AS history_schema
              ON history_schema.schema_id = history_table.schema_id
            WHERE current_table.object_id = OBJECT_ID(
                      N'{schema}.{_CURRENT_TABLE}', N'U'
                  )
              AND current_table.temporal_type = 2
              AND history_table.object_id = OBJECT_ID(
                      N'{schema}.{_HISTORY_TABLE}', N'U'
                  )
              AND history_schema.name = N'{schema}'
              AND current_table.history_retention_period = 24
              AND current_table.history_retention_period_unit_desc = N'MONTH'
        )
        BEGIN
            ;THROW 51011,
                'Integrity schema migration validation failed: temporal history or retention is incorrect.',
                1;
        END;

        IF NOT EXISTS (
            SELECT 1 FROM sys.tables
            WHERE object_id = OBJECT_ID(N'{schema}.NomGraph_Person', N'U')
              AND is_node = 1
        )
           OR NOT EXISTS (
            SELECT 1 FROM sys.tables
            WHERE object_id = OBJECT_ID(N'{schema}.NomGraph_Nominated', N'U')
              AND is_edge = 1
        )
        BEGIN
            ;THROW 51012,
                'Integrity schema migration validation failed: SQL Graph metadata is incorrect.',
                1;
        END;

        IF EXISTS (
            SELECT 1
            FROM sys.foreign_keys AS foreign_key
            WHERE (
                foreign_key.parent_object_id IN (
                    SELECT OBJECT_ID(
                        QUOTENAME(N'{schema}') + N'.' + QUOTENAME(target.TableName),
                        N'U'
                    )
                    FROM @Targets AS target
                )
                OR foreign_key.referenced_object_id IN (
                    SELECT OBJECT_ID(
                        QUOTENAME(N'{schema}') + N'.' + QUOTENAME(target.TableName),
                        N'U'
                    )
                    FROM @Targets AS target
                )
            )
              AND (
                  foreign_key.is_disabled = 1
                  OR foreign_key.is_not_trusted = 1
              )
        )
        BEGIN
            ;THROW 51013,
                'Integrity schema migration validation failed: an affected foreign key is disabled or untrusted.',
                1;
        END;
    """


def _transfer(source_schema: str, destination_schema: str) -> None:
    op.execute(_preflight_sql(source_schema, destination_schema))

    op.execute(
        f"ALTER TABLE [{source_schema}].[{_CURRENT_TABLE}] "
        "SET (SYSTEM_VERSIONING = OFF);"
    )
    op.execute(
        f"ALTER SCHEMA [{destination_schema}] "
        f"TRANSFER [{source_schema}].[{_HISTORY_TABLE}];"
    )
    op.execute(
        f"ALTER SCHEMA [{destination_schema}] "
        f"TRANSFER [{source_schema}].[{_CURRENT_TABLE}];"
    )

    for table in _NON_TEMPORAL_TABLES:
        op.execute(
            f"ALTER SCHEMA [{destination_schema}] "
            f"TRANSFER [{source_schema}].[{table}];"
        )

    op.execute(f"""
        ALTER TABLE [{destination_schema}].[{_CURRENT_TABLE}]
        SET (
            SYSTEM_VERSIONING = ON (
                HISTORY_TABLE = [{destination_schema}].[{_HISTORY_TABLE}],
                DATA_CONSISTENCY_CHECK = ON,
                HISTORY_RETENTION_PERIOD = 24 MONTHS
            )
        );
    """)
    op.execute(_validate_sql(destination_schema, source_schema))


def upgrade() -> None:
    op.execute("""
        IF SCHEMA_ID(N'integrity') IS NULL
        BEGIN
            EXEC(N'CREATE SCHEMA [integrity] AUTHORIZATION [dbo]');
        END;
    """)
    _transfer("dbo", "integrity")


def downgrade() -> None:
    _transfer("integrity", "dbo")
    op.execute("""
        IF SCHEMA_ID(N'integrity') IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
               FROM sys.objects
               WHERE schema_id = SCHEMA_ID(N'integrity')
           )
        BEGIN
            DROP SCHEMA [integrity];
        END;
    """)
