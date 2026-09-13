"""SQL Server adapter for Synthetics Inc. tenant configuration.

Phase B deliberately provisions configuration only. It does not create users,
nominations, integrity decisions, logs, model artifacts, or Service Bus events.
All writes run in one transaction and are rolled back on any conflict.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from collections.abc import Callable
from typing import Any

from .scenarios import GENERATOR_VERSION, SyntheticNomination, SyntheticUser


ACTOR = "svc:synthetic-tenant-seeder:v1"
SOURCE_TENANT_ID = 1
ORGANIZATION_ID = "f74bff31-f42f-4461-a1dd-e1ae978c1abe"
TENANT_NAME = "Synthetics Inc"
DOMAIN = "synthetic-awards.terianix.ai"
SITE_URL = f"https://{DOMAIN}"
LOGO_URL = f"{SITE_URL}/synthetics-inc-logo.png"
TAGLINE = "Recognition, intelligently composed."

REQUIRED_COLUMNS = {
    "Tenants": {
        "TenantId", "AzureAdTenantId", "Config", "Domain", "Site_URL",
        "Company_Logo_URL", "Tagline", "fallback_admin_email",
        "desc_check_config", "certificate_config", "integrity_config",
        "payroll_provider_id", "is_demo", "is_synthetic",
    },
    "IntegrityDecisionResults": {
        "TrainingDispositionSource",
        "TrainingDispositionMetadataJson",
    },
}


@dataclass(frozen=True)
class ConfigurationResult:
    tenant_id: int
    created_tenant: bool
    category_id_map: dict[int, int]
    category_count: int
    email_template_count: int
    graph_pattern_count: int
    hashes: dict[str, str]


@dataclass(frozen=True)
class CorpusResult:
    sql_user_count: int
    nomination_count: int
    decision_count: int
    inserted_nomination_count: int
    generation_run_id: str
    sql_user_ids_by_logical_id: dict[str, int]
    admin_sql_user_id: int


def connect_from_environment():
    """Open a non-autocommit pyodbc connection from the standard SQL variables."""
    import pyodbc

    required = ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise EnvironmentError(f"Missing SQL configuration: {', '.join(missing)}")
    driver = os.getenv("DB_DRIVER", "{ODBC Driver 18 for SQL Server}")
    connection_string = (
        f"Driver={driver};Server={os.environ['SQL_SERVER']};"
        f"Database={os.environ['SQL_DATABASE']};UID={os.environ['SQL_USER']};"
        f"PWD={os.environ['SQL_PASSWORD']};Encrypt=yes;"
        "TrustServerCertificate=no;Connection Timeout=60;"
    )
    return pyodbc.connect(connection_string, autocommit=False)


def _rows(cursor) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _row(cursor) -> dict[str, Any] | None:
    rows = _rows(cursor)
    if not rows:
        return None
    if len(rows) != 1:
        raise RuntimeError(f"Expected one row, found {len(rows)}")
    return rows[0]


def _columns(cursor, table: str) -> set[str]:
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=?",
        table,
    )
    return {str(row[0]) for row in cursor.fetchall()}


def assert_schema(cursor) -> None:
    """Fail before any write when migration 0060 or core tables are absent."""
    for table, required in REQUIRED_COLUMNS.items():
        available = _columns(cursor, table)
        missing = required - available
        if missing:
            raise RuntimeError(
                f"dbo.{table} is missing {sorted(missing)}; migration 0060 is required"
            )
    for table in (
        "nomination_categories",
        "EmailTemplates",
        "GraphScoringPolicies",
        "GraphScoringPatternParameters",
        "GNNScoringPolicies",
    ):
        if not _columns(cursor, table):
            raise RuntimeError(f"Required table dbo.{table} does not exist")


def _sha256(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            pass
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tenant_configuration(cursor) -> tuple[int, bool, dict[str, str]]:
    cursor.execute(
        """
        SELECT TenantId, TenantName, AzureAdTenantId, Config, desc_check_config,
               certificate_config, integrity_config, is_demo
        FROM dbo.Tenants WHERE TenantId=?
        """,
        SOURCE_TENANT_ID,
    )
    source = _row(cursor)
    if source is None:
        raise RuntimeError("Source Tenant 1 does not exist")

    cursor.execute(
        """
        SELECT TenantId, TenantName, AzureAdTenantId, Domain, is_synthetic
        FROM dbo.Tenants
        WHERE AzureAdTenantId=? OR TenantName=? OR Domain=?
        """,
        ORGANIZATION_ID,
        TENANT_NAME,
        DOMAIN,
    )
    matches = _rows(cursor)
    if len(matches) > 1:
        raise RuntimeError(
            "Destination identifiers resolve to multiple tenant rows; refusing to merge"
        )

    created = not matches
    if matches:
        destination = matches[0]
        conflicts = {
            "TenantName": destination["TenantName"] != TENANT_NAME,
            "AzureAdTenantId": destination["AzureAdTenantId"] != ORGANIZATION_ID,
            "Domain": destination["Domain"] not in (None, DOMAIN),
        }
        conflicting = [key for key, value in conflicts.items() if value]
        if conflicting:
            raise RuntimeError(
                f"Existing destination conflicts on {conflicting}; refusing to overwrite"
            )
        tenant_id = int(destination["TenantId"])
        if not bool(destination["is_synthetic"]):
            raise RuntimeError(
                "Existing Synthetics Inc. row is not marked is_synthetic; set the "
                "flag deliberately before provisioning"
            )
    else:
        cursor.execute(
            """
            INSERT INTO dbo.Tenants (
                TenantName, AzureAdTenantId, Config, Domain, Site_URL,
                Company_Logo_URL, Tagline, fallback_admin_email,
                desc_check_config, certificate_config, integrity_config,
                payroll_provider_id, is_demo, is_synthetic,
                created_by, updated_by
            )
            OUTPUT INSERTED.TenantId
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, 1, ?, ?)
            """,
            TENANT_NAME,
            ORGANIZATION_ID,
            source["Config"],
            DOMAIN,
            SITE_URL,
            LOGO_URL,
            TAGLINE,
            source["desc_check_config"],
            source["certificate_config"],
            source["integrity_config"],
            source["is_demo"],
            ACTOR,
            ACTOR,
        )
        tenant_id = int(cursor.fetchone()[0])

    cursor.execute(
        """
        UPDATE dbo.Tenants
        SET Config=?, Domain=?, Site_URL=?, Company_Logo_URL=?, Tagline=?,
            fallback_admin_email=NULL, desc_check_config=?, certificate_config=?,
            integrity_config=?, payroll_provider_id=NULL, is_demo=?,
            is_synthetic=1, updated_at=SYSUTCDATETIME(), updated_by=?
        WHERE TenantId=?
        """,
        source["Config"],
        DOMAIN,
        SITE_URL,
        LOGO_URL,
        TAGLINE,
        source["desc_check_config"],
        source["certificate_config"],
        source["integrity_config"],
        source["is_demo"],
        ACTOR,
        tenant_id,
    )
    hashes = {
        name: _sha256(source[name])
        for name in (
            "Config",
            "desc_check_config",
            "certificate_config",
            "integrity_config",
        )
    }
    return tenant_id, created, hashes


def _clone_categories(cursor, tenant_id: int) -> dict[int, int]:
    cursor.execute(
        """
        SELECT id, category_description, min_amount, max_amount, is_active
        FROM dbo.nomination_categories WHERE tenant_id=? ORDER BY id
        """,
        SOURCE_TENANT_ID,
    )
    source = _rows(cursor)
    if not source:
        raise RuntimeError("Tenant 1 has no nomination categories to clone")
    cursor.execute(
        """
        SELECT id, category_description, min_amount, max_amount, is_active
        FROM dbo.nomination_categories WHERE tenant_id=? ORDER BY id
        """,
        tenant_id,
    )
    destination_rows = _rows(cursor)
    destination_by_name = {
        row["category_description"]: row for row in destination_rows
    }
    if len(destination_by_name) != len(destination_rows):
        raise RuntimeError("Destination contains duplicate category descriptions")

    id_map: dict[int, int] = {}
    source_names = {row["category_description"] for row in source}
    extras = set(destination_by_name) - source_names
    if extras:
        raise RuntimeError(
            f"Destination has categories not present on Tenant 1: {sorted(extras)}"
        )
    for row in source:
        existing = destination_by_name.get(row["category_description"])
        if existing:
            cursor.execute(
                """
                UPDATE dbo.nomination_categories
                SET min_amount=?, max_amount=?, is_active=?,
                    updated_at=SYSUTCDATETIME(), updated_by=?
                WHERE id=? AND tenant_id=?
                """,
                row["min_amount"], row["max_amount"], row["is_active"],
                ACTOR, existing["id"], tenant_id,
            )
            new_id = int(existing["id"])
        else:
            cursor.execute(
                """
                INSERT INTO dbo.nomination_categories (
                    tenant_id, category_description, min_amount, max_amount,
                    is_active, created_by, updated_by
                ) OUTPUT INSERTED.id VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tenant_id, row["category_description"], row["min_amount"],
                row["max_amount"], row["is_active"], ACTOR, ACTOR,
            )
            new_id = int(cursor.fetchone()[0])
        id_map[int(row["id"])] = new_id
    return id_map


def _clone_email_templates(cursor, tenant_id: int) -> int:
    cursor.execute(
        """
        SELECT TemplateKey, Lang, Subject, BodyTemplate, Version
        FROM dbo.EmailTemplates WHERE TenantId=? ORDER BY TemplateKey, Lang
        """,
        SOURCE_TENANT_ID,
    )
    rows = _rows(cursor)
    for row in rows:
        cursor.execute(
            """
            SELECT TemplateId FROM dbo.EmailTemplates
            WHERE TenantId=? AND TemplateKey=? AND Lang=?
            """,
            tenant_id, row["TemplateKey"], row["Lang"],
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                """
                UPDATE dbo.EmailTemplates
                SET Subject=?, BodyTemplate=?, Active=0, Version=?,
                    updated_at=SYSUTCDATETIME(), updated_by=?
                WHERE TemplateId=?
                """,
                row["Subject"], row["BodyTemplate"], row["Version"],
                ACTOR, existing[0],
            )
        else:
            cursor.execute(
                """
                INSERT INTO dbo.EmailTemplates (
                    TenantId, TemplateKey, Lang, Subject, BodyTemplate,
                    Active, Version, created_by, updated_by
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                tenant_id, row["TemplateKey"], row["Lang"], row["Subject"],
                row["BodyTemplate"], row["Version"], ACTOR, ACTOR,
            )
    return len(rows)


def _same_policy(actual: dict[str, Any], expected: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(actual.get(field) == expected.get(field) for field in fields)


def _clone_graph_policy(cursor, tenant_id: int) -> int:
    fields = (
        "ScoringStrategy", "LowThreshold", "MediumThreshold", "HighThreshold",
        "CriticalThreshold", "DetectionWindowDays", "SnapshotMaxAgeDays",
    )
    cursor.execute(
        f"SELECT PolicyId, {', '.join(fields)} FROM dbo.GraphScoringPolicies "
        "WHERE TenantId=? AND Status='ACTIVE'",
        SOURCE_TENANT_ID,
    )
    source = _row(cursor)
    if source is None:
        raise RuntimeError("Tenant 1 has no active Graph policy")
    cursor.execute(
        f"SELECT PolicyId, {', '.join(fields)} FROM dbo.GraphScoringPolicies "
        "WHERE TenantId=? AND Status='ACTIVE'",
        tenant_id,
    )
    destination = _row(cursor)
    if destination and not _same_policy(destination, source, fields):
        raise RuntimeError("Existing active destination Graph policy differs from Tenant 1")
    if destination:
        policy_id = int(destination["PolicyId"])
    else:
        values = [source[field] for field in fields]
        cursor.execute(
            f"""
            INSERT INTO dbo.GraphScoringPolicies (
                TenantId, PolicyVersion, Status, {', '.join(fields)},
                CreatedBy, UpdatedBy, PublishedAt, PublishedBy
            ) OUTPUT INSERTED.PolicyId
            VALUES (?, 1, 'ACTIVE', {', '.join('?' for _ in fields)},
                    ?, ?, SYSUTCDATETIME(), ?)
            """,
            tenant_id, *values, ACTOR, ACTOR, ACTOR,
        )
        policy_id = int(cursor.fetchone()[0])

    cursor.execute(
        """
        SELECT PatternType, DisplayOrder, Enabled, EnabledForRouting,
               ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
               ParametersJson, CandidateEvaluationJson
        FROM dbo.GraphScoringPatternParameters WHERE PolicyId=? ORDER BY PatternType
        """,
        source["PolicyId"],
    )
    patterns = _rows(cursor)
    cursor.execute(
        """
        SELECT PatternType, DisplayOrder, Enabled, EnabledForRouting,
               ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
               ParametersJson, CandidateEvaluationJson
        FROM dbo.GraphScoringPatternParameters WHERE PolicyId=? ORDER BY PatternType
        """,
        policy_id,
    )
    destination_patterns = _rows(cursor)
    destination_count = len(destination_patterns)
    if destination_count not in (0, len(patterns)):
        raise RuntimeError("Destination Graph policy has a partial parameter set")
    if destination_count and destination_patterns != patterns:
        raise RuntimeError(
            "Existing destination Graph pattern parameters differ from Tenant 1"
        )
    if destination_count == 0:
        for row in patterns:
            cursor.execute(
                """
                INSERT INTO dbo.GraphScoringPatternParameters (
                    PolicyId, PatternType, DisplayOrder, Enabled, EnabledForRouting,
                    ApplicableRolesJson, BaseScore, MinimumScore, MaximumScore,
                    ParametersJson, CandidateEvaluationJson, CreatedBy, UpdatedBy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                policy_id, row["PatternType"], row["DisplayOrder"],
                row["Enabled"], row["EnabledForRouting"],
                row["ApplicableRolesJson"], row["BaseScore"],
                row["MinimumScore"], row["MaximumScore"],
                row["ParametersJson"], row["CandidateEvaluationJson"],
                ACTOR, ACTOR,
            )
    return len(patterns)


def _configuration_hashes(cursor, tenant_id: int) -> dict[str, str]:
    """Hash normalized source configuration slices without identity columns."""
    hashes: dict[str, str] = {}
    slices = {
        "categories": (
            """
            SELECT category_description, min_amount, max_amount, is_active
            FROM dbo.nomination_categories WHERE tenant_id=?
            ORDER BY category_description
            """,
            SOURCE_TENANT_ID,
        ),
        "email_templates": (
            """
            SELECT TemplateKey, Lang, Subject, BodyTemplate, Version
            FROM dbo.EmailTemplates WHERE TenantId=?
            ORDER BY TemplateKey, Lang
            """,
            SOURCE_TENANT_ID,
        ),
        "graph_policy": (
            """
            SELECT ScoringStrategy, LowThreshold, MediumThreshold, HighThreshold,
                   CriticalThreshold, DetectionWindowDays, SnapshotMaxAgeDays
            FROM dbo.GraphScoringPolicies
            WHERE TenantId=? AND Status='ACTIVE'
            """,
            SOURCE_TENANT_ID,
        ),
        "graph_pattern_parameters": (
            """
            SELECT x.PatternType, x.DisplayOrder, x.Enabled, x.EnabledForRouting,
                   x.ApplicableRolesJson, x.BaseScore, x.MinimumScore,
                   x.MaximumScore, x.ParametersJson, x.CandidateEvaluationJson
            FROM dbo.GraphScoringPatternParameters x
            JOIN dbo.GraphScoringPolicies p ON p.PolicyId=x.PolicyId
            WHERE p.TenantId=? AND p.Status='ACTIVE'
            ORDER BY x.PatternType
            """,
            SOURCE_TENANT_ID,
        ),
        "destination_categories": (
            """
            SELECT category_description, min_amount, max_amount, is_active
            FROM dbo.nomination_categories WHERE tenant_id=?
            ORDER BY category_description
            """,
            tenant_id,
        ),
        "destination_email_templates": (
            """
            SELECT TemplateKey, Lang, Subject, BodyTemplate, Version
            FROM dbo.EmailTemplates WHERE TenantId=?
            ORDER BY TemplateKey, Lang
            """,
            tenant_id,
        ),
        "destination_graph_policy": (
            """
            SELECT ScoringStrategy, LowThreshold, MediumThreshold, HighThreshold,
                   CriticalThreshold, DetectionWindowDays, SnapshotMaxAgeDays
            FROM dbo.GraphScoringPolicies
            WHERE TenantId=? AND Status='ACTIVE'
            """,
            tenant_id,
        ),
        "destination_graph_pattern_parameters": (
            """
            SELECT x.PatternType, x.DisplayOrder, x.Enabled, x.EnabledForRouting,
                   x.ApplicableRolesJson, x.BaseScore, x.MinimumScore,
                   x.MaximumScore, x.ParametersJson, x.CandidateEvaluationJson
            FROM dbo.GraphScoringPatternParameters x
            JOIN dbo.GraphScoringPolicies p ON p.PolicyId=x.PolicyId
            WHERE p.TenantId=? AND p.Status='ACTIVE'
            ORDER BY x.PatternType
            """,
            tenant_id,
        ),
    }
    for name, (query, parameter) in slices.items():
        cursor.execute(query, parameter)
        hashes[name] = _sha256(_rows(cursor))
    if hashes["categories"] != hashes["destination_categories"]:
        raise RuntimeError("Destination category clone does not match Tenant 1")
    if hashes["email_templates"] != hashes["destination_email_templates"]:
        raise RuntimeError("Destination email template clone does not match Tenant 1")
    if hashes["graph_policy"] != hashes["destination_graph_policy"]:
        raise RuntimeError("Destination Graph policy clone does not match Tenant 1")
    if (
        hashes["graph_pattern_parameters"]
        != hashes["destination_graph_pattern_parameters"]
    ):
        raise RuntimeError("Destination Graph parameter clone does not match Tenant 1")
    return hashes


def _gnn_configuration(raw: str) -> str:
    configuration = json.loads(raw)
    if not isinstance(configuration, dict):
        raise RuntimeError("Tenant 1 GNN ConfigurationJson must be an object")
    cloned = deepcopy(configuration)
    training = cloned.get("training")
    if not isinstance(training, dict):
        raise RuntimeError("Tenant 1 GNN policy has no training object")
    training["window_days"] = 365
    return json.dumps(cloned, separators=(",", ":"), sort_keys=True)


def _clone_gnn_policy(cursor, tenant_id: int) -> str:
    cursor.execute(
        """
        SELECT PolicyId, TrainingEnabled, InferenceEnabled, ConfigurationJson,
               ExplanationEnabled, ExplanationMinimumRisk
        FROM dbo.GNNScoringPolicies
        WHERE TenantId=? AND Status='ACTIVE'
        """,
        SOURCE_TENANT_ID,
    )
    source = _row(cursor)
    if source is None:
        raise RuntimeError("Tenant 1 has no active GNN policy")
    configuration = _gnn_configuration(source["ConfigurationJson"])
    cursor.execute(
        """
        SELECT PolicyId, TrainingEnabled, InferenceEnabled, ConfigurationJson,
               ExplanationEnabled, ExplanationMinimumRisk
        FROM dbo.GNNScoringPolicies
        WHERE TenantId=? AND Status='ACTIVE'
        """,
        tenant_id,
    )
    destination = _row(cursor)
    expected = {
        **source,
        "ConfigurationJson": configuration,
    }
    fields = (
        "TrainingEnabled", "InferenceEnabled", "ConfigurationJson",
        "ExplanationEnabled", "ExplanationMinimumRisk",
    )
    if destination:
        comparable_destination = {
            **destination,
            "ConfigurationJson": json.dumps(
                json.loads(destination["ConfigurationJson"]),
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
        if not _same_policy(comparable_destination, expected, fields):
            raise RuntimeError("Existing active destination GNN policy differs from the approved clone")
    else:
        cursor.execute(
            """
            INSERT INTO dbo.GNNScoringPolicies (
                TenantId, PolicyVersion, Status, TrainingEnabled,
                InferenceEnabled, ConfigurationJson, ExplanationEnabled,
                ExplanationMinimumRisk, CreatedBy, UpdatedBy,
                PublishedAt, PublishedBy
            ) VALUES (?, 1, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME(), ?)
            """,
            tenant_id, source["TrainingEnabled"], source["InferenceEnabled"],
            configuration, source["ExplanationEnabled"],
            source["ExplanationMinimumRisk"], ACTOR, ACTOR, ACTOR,
        )
    return _sha256(json.loads(configuration))


def provision_configuration(conn) -> ConfigurationResult:
    """Clone approved Tenant 1 configuration in a single SQL transaction."""
    cursor = conn.cursor()
    try:
        assert_schema(cursor)
        tenant_id, created, hashes = _tenant_configuration(cursor)
        category_map = _clone_categories(cursor, tenant_id)
        template_count = _clone_email_templates(cursor, tenant_id)
        pattern_count = _clone_graph_policy(cursor, tenant_id)
        hashes["gnn_policy"] = _clone_gnn_policy(cursor, tenant_id)
        hashes.update(_configuration_hashes(cursor, tenant_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ConfigurationResult(
        tenant_id=tenant_id,
        created_tenant=created,
        category_id_map=category_map,
        category_count=len(category_map),
        email_template_count=template_count,
        graph_pattern_count=pattern_count,
        hashes=hashes,
    )


def _provision_sql_users(
    cursor,
    tenant_id: int,
    users: list[SyntheticUser],
) -> tuple[dict[str, int], int]:
    admin_upn = "david64.terian@synthetics.terian-services.com"
    expected_upns = {user.upn for user in users} | {admin_upn}
    cursor.execute(
        """
        SELECT UserId, userPrincipalName, FirstName, LastName, Title
        FROM dbo.Users WHERE TenantId=?
        """,
        tenant_id,
    )
    existing_rows = _rows(cursor)
    existing_by_upn = {
        str(row["userPrincipalName"]).lower(): row for row in existing_rows
    }
    if len(existing_by_upn) != len(existing_rows):
        raise RuntimeError("Destination contains duplicate normalized SQL UPNs")
    extras = set(existing_by_upn) - expected_upns
    if extras:
        raise RuntimeError(
            f"Destination has {len(extras)} SQL users outside the approved roster"
        )

    user_id_by_logical: dict[str, int] = {}
    for user in users:
        existing = existing_by_upn.get(user.upn)
        if existing:
            cursor.execute(
                """
                UPDATE dbo.Users
                SET userEmail=NULL, FirstName=?, LastName=?, Title=?,
                    updated_at=SYSUTCDATETIME(), updated_by=?
                WHERE UserId=? AND TenantId=?
                """,
                user.first_name, user.last_name, user.department, ACTOR,
                existing["UserId"], tenant_id,
            )
            user_id = int(existing["UserId"])
        else:
            cursor.execute(
                """
                INSERT INTO dbo.Users (
                    userPrincipalName, userEmail, FirstName, LastName, Title,
                    ManagerId, TenantId, created_by, updated_by
                ) OUTPUT INSERTED.UserId
                VALUES (?, NULL, ?, ?, ?, NULL, ?, ?, ?)
                """,
                user.upn, user.first_name, user.last_name, user.department,
                tenant_id, ACTOR, ACTOR,
            )
            user_id = int(cursor.fetchone()[0])
        user_id_by_logical[user.logical_id] = user_id

    admin = existing_by_upn.get(admin_upn)
    if admin:
        cursor.execute(
            """
            UPDATE dbo.Users
            SET userEmail=NULL, FirstName='David64', LastName='Terian',
                Title='Executive Leadership', ManagerId=NULL,
                updated_at=SYSUTCDATETIME(), updated_by=?
            WHERE UserId=? AND TenantId=?
            """,
            ACTOR, admin["UserId"], tenant_id,
        )
        admin_user_id = int(admin["UserId"])
    else:
        cursor.execute(
            """
            INSERT INTO dbo.Users (
                userPrincipalName, userEmail, FirstName, LastName, Title,
                ManagerId, TenantId, created_by, updated_by
            ) OUTPUT INSERTED.UserId
            VALUES (?, NULL, 'David64', 'Terian', 'Executive Leadership',
                    NULL, ?, ?, ?)
            """,
            admin_upn, tenant_id, ACTOR, ACTOR,
        )
        admin_user_id = int(cursor.fetchone()[0])

    for user in users:
        manager_id = (
            user_id_by_logical[user.manager_logical_id]
            if user.manager_logical_id else None
        )
        cursor.execute(
            """
            UPDATE dbo.Users SET ManagerId=?, updated_at=SYSUTCDATETIME(),
                updated_by=? WHERE UserId=? AND TenantId=?
            """,
            manager_id, ACTOR, user_id_by_logical[user.logical_id], tenant_id,
        )
    return user_id_by_logical, admin_user_id


def _categories_by_name(cursor, tenant_id: int) -> dict[str, dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, category_description, min_amount, max_amount
        FROM dbo.nomination_categories WHERE tenant_id=?
        """,
        tenant_id,
    )
    rows = _rows(cursor)
    result = {str(row["category_description"]): row for row in rows}
    if len(result) != len(rows):
        raise RuntimeError("Destination category descriptions are not unique")
    return result


def _engine_not_run() -> str:
    return json.dumps({
        "available": False,
        "status": "NOT_RUN",
        "unavailable_reason": "SYNTHETIC_HISTORICAL_IMPORT",
    }, separators=(",", ":"), sort_keys=True)


def _corpus_stage_values(
    *,
    row: SyntheticNomination,
    user_ids: dict[str, int],
    category_id: int,
    generation_run_id: str,
    corpus_sha256: str,
    seed: int,
) -> tuple[Any, ...]:
    timestamp = datetime.fromisoformat(row.nomination_time_utc)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    approved_at = timestamp if row.status in ("Approved", "Paid") else None
    paid_at = timestamp if row.status == "Paid" else None
    rejection_reason = (
        "Historical synthetic scenario outcome" if row.status == "Rejected" else None
    )
    rejection_actor = "Synthetic Ground Truth" if row.status == "Rejected" else None
    metadata = json.dumps({
        "generator_version": GENERATOR_VERSION,
        "generation_run_id": generation_run_id,
        "corpus_sha256": corpus_sha256,
        "seed": seed,
        "logical_nomination_id": row.logical_id,
        "stable_nomination_id": row.stable_id,
        "temporal_segment": row.segment,
        "scenario_family": row.scenario_family,
        "scenario_variant": row.scenario_variant,
    }, separators=(",", ":"), sort_keys=True)
    return (
        f"synthetic:{row.stable_id}",
        user_ids[row.nominator_logical_id],
        user_ids[row.beneficiary_logical_id],
        user_ids[row.approver_logical_id],
        row.amount,
        row.description,
        timestamp,
        row.status,
        approved_at,
        paid_at,
        category_id,
        rejection_reason,
        rejection_actor,
        timestamp,
        timestamp,
        ACTOR,
        ACTOR,
        row.training_disposition,
        metadata,
    )


def _insert_corpus_batch(cursor, rows: list[tuple[Any, ...]]) -> None:
    """Stage up to 100 records in one request (under SQL Server's parameter cap)."""
    if not rows:
        return
    placeholders = "(" + ",".join("?" for _ in rows[0]) + ")"
    cursor.execute(
        """
        INSERT INTO #SyntheticCorpusStage (
            SourceMessageId, NominatorId, BeneficiaryId, ApproverId, Amount,
            NominationDescription, NominationDate, Status, ApprovedDate,
            PayedDate, CategoryId, RejectionReason, RejectionActor,
            CreatedAt, UpdatedAt, CreatedBy, UpdatedBy,
            TrainingDisposition, TrainingDispositionMetadataJson
        ) VALUES
        """ + ",".join(placeholders for _ in rows),
        *(value for row in rows for value in row),
    )


def provision_corpus(
    conn,
    *,
    tenant_id: int,
    users: list[SyntheticUser],
    nominations: list[SyntheticNomination],
    corpus_sha256: str,
    seed: int,
    generation_run_id: str,
    progress: Callable[[str], None] | None = None,
) -> CorpusResult:
    """Reconcile SQL users and atomically load nominations plus truth envelopes."""
    report = progress or (lambda _message: None)
    cursor = conn.cursor()
    try:
        assert_schema(cursor)
        cursor.execute(
            """
            SELECT TenantName, AzureAdTenantId, Domain, is_synthetic
            FROM dbo.Tenants WHERE TenantId=?
            """,
            tenant_id,
        )
        tenant = _row(cursor)
        if not tenant or (
            tenant["TenantName"] != TENANT_NAME
            or tenant["AzureAdTenantId"] != ORGANIZATION_ID
            or tenant["Domain"] != DOMAIN
            or not bool(tenant["is_synthetic"])
        ):
            raise RuntimeError("Destination tenant failed the synthetic identity preflight")

        user_ids, admin_user_id = _provision_sql_users(cursor, tenant_id, users)
        report("SQL users reconciled: 401/401")
        categories = _categories_by_name(cursor, tenant_id)
        missing_categories = {
            row.category_name for row in nominations if row.category_name not in categories
        }
        if missing_categories:
            raise RuntimeError(
                f"Generated categories are absent from Tenant 1 clone: {sorted(missing_categories)}"
            )
        for index, row in enumerate(nominations, start=1):
            category = categories[row.category_name]
            minimum = category["min_amount"]
            maximum = category["max_amount"]
            if minimum is not None and row.amount < int(minimum):
                raise RuntimeError(
                    f"{row.logical_id} amount {row.amount} is below the category minimum"
                )
            if maximum is not None and row.amount > int(maximum):
                raise RuntimeError(
                    f"{row.logical_id} amount {row.amount} exceeds the category maximum"
                )

        cursor.execute(
            """
            SELECT SourceMessageId, NominationId, TrainingDisposition,
                   TrainingDispositionMetadataJson
            FROM dbo.IntegrityDecisionResults
            WHERE TenantId=? AND SourceMessageId LIKE 'synthetic:%'
            """,
            tenant_id,
        )
        existing = {row["SourceMessageId"]: row for row in _rows(cursor)}
        expected_source_ids = {f"synthetic:{row.stable_id}" for row in nominations}
        extras = set(existing) - expected_source_ids
        if extras:
            raise RuntimeError(
                f"Destination contains {len(extras)} unrecognized synthetic decisions"
            )

        cursor.execute(
            """
            CREATE TABLE #SyntheticCorpusStage (
                SourceMessageId NVARCHAR(200) NOT NULL PRIMARY KEY,
                NominatorId INT NOT NULL,
                BeneficiaryId INT NOT NULL,
                ApproverId INT NOT NULL,
                Amount INT NOT NULL,
                NominationDescription NVARCHAR(MAX) NOT NULL,
                NominationDate DATETIME2 NOT NULL,
                Status NVARCHAR(100) NOT NULL,
                ApprovedDate DATETIME2 NULL,
                PayedDate DATETIME2 NULL,
                CategoryId INT NOT NULL,
                RejectionReason NVARCHAR(MAX) NULL,
                RejectionActor NVARCHAR(255) NULL,
                CreatedAt DATETIME2 NOT NULL,
                UpdatedAt DATETIME2 NOT NULL,
                CreatedBy NVARCHAR(255) NOT NULL,
                UpdatedBy NVARCHAR(255) NOT NULL,
                TrainingDisposition NVARCHAR(50) NOT NULL,
                TrainingDispositionMetadataJson NVARCHAR(MAX) NOT NULL
            )
            """
        )
        staged_rows: list[tuple[Any, ...]] = []
        staged_count = 0
        for index, row in enumerate(nominations, start=1):
            source_id = f"synthetic:{row.stable_id}"
            current = existing.get(source_id)
            if current:
                try:
                    metadata = json.loads(current["TrainingDispositionMetadataJson"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"Existing decision {source_id} has invalid provenance JSON"
                    ) from exc
                if (
                    current["TrainingDisposition"] != row.training_disposition
                    or metadata.get("corpus_sha256") != corpus_sha256
                    or metadata.get("generation_run_id") != generation_run_id
                ):
                    raise RuntimeError(
                        f"Existing decision {source_id} conflicts with this corpus"
                    )
            else:
                staged_rows.append(_corpus_stage_values(
                    row=row,
                    user_ids=user_ids,
                    category_id=int(categories[row.category_name]["id"]),
                    generation_run_id=generation_run_id,
                    corpus_sha256=corpus_sha256,
                    seed=seed,
                ))
                staged_count += 1
                if len(staged_rows) == 100:
                    _insert_corpus_batch(cursor, staged_rows)
                    staged_rows.clear()
            if index % 500 == 0 or index == len(nominations):
                report(
                    f"SQL nominations staged: {index}/{len(nominations)} "
                    f"({staged_count} new)"
                )
        _insert_corpus_batch(cursor, staged_rows)

        cursor.execute(
            "SELECT COUNT(*) FROM #SyntheticCorpusStage"
        )
        inserted = int(cursor.fetchone()[0])
        if inserted:
            cursor.execute(
                """
                CREATE TABLE #SyntheticNominationMap (
                    SourceMessageId NVARCHAR(200) NOT NULL PRIMARY KEY,
                    NominationId INT NOT NULL UNIQUE
                );

                MERGE INTO dbo.Nominations AS destination
                USING #SyntheticCorpusStage AS source
                    ON 1 = 0
                WHEN NOT MATCHED BY TARGET THEN
                    INSERT (
                        NominatorId, BeneficiaryId, ApproverId, Amount,
                        NominationDescription, NominationDate, Status,
                        ApprovedDate, PayedDate, Currency, CategoryId,
                        ApproverNotifiedAt, RejectionReason, RejectionActor,
                        created_at, updated_at, created_by, updated_by
                    ) VALUES (
                        source.NominatorId, source.BeneficiaryId,
                        source.ApproverId, source.Amount,
                        source.NominationDescription, source.NominationDate,
                        source.Status, source.ApprovedDate, source.PayedDate,
                        'USD', source.CategoryId, NULL,
                        source.RejectionReason, source.RejectionActor,
                        source.CreatedAt, source.UpdatedAt,
                        source.CreatedBy, source.UpdatedBy
                    )
                OUTPUT source.SourceMessageId, inserted.NominationId
                    INTO #SyntheticNominationMap (SourceMessageId, NominationId);
                """
            )
            unavailable = _engine_not_run()
            cursor.execute(
                """
                INSERT INTO dbo.IntegrityDecisionResults (
                    NominationId, TenantId, DecisionSchemaVersion,
                    PolicyVersion, SourceMessageId, RfResultJson,
                    GraphResultJson, GnnResultJson, SemanticResultJson,
                    CompositeScore, CompositeRiskLevel,
                    DecisiveEnginesJson, FinalRoute, RoutingRule,
                    ReviewScope, HumanReviewOutcome, TrainingDisposition,
                    TrainingDispositionSource,
                    TrainingDispositionMetadataJson, ReviewReason,
                    ReviewedBy, ReviewedAt, ScoredBy, CreatedAt, UpdatedAt
                )
                SELECT
                    mapping.NominationId, ?, 2, 'synthetic-historical-v1',
                    stage.SourceMessageId, ?, ?, ?, ?, NULL, 'UNKNOWN', '[]',
                    'MANAGER_APPROVAL', 'SYNTHETIC_HISTORICAL_IMPORT', NULL,
                    NULL, stage.TrainingDisposition,
                    'SYNTHETIC_GROUND_TRUTH',
                    stage.TrainingDispositionMetadataJson,
                    NULL, NULL, NULL, ?, stage.CreatedAt, stage.UpdatedAt
                FROM #SyntheticCorpusStage AS stage
                INNER JOIN #SyntheticNominationMap AS mapping
                    ON mapping.SourceMessageId = stage.SourceMessageId
                """,
                tenant_id,
                unavailable,
                unavailable,
                unavailable,
                unavailable,
                ACTOR,
            )
            report(f"SQL nominations and decisions inserted: {inserted}")

        cursor.execute(
            "SELECT COUNT(*) FROM dbo.Users WHERE TenantId=?",
            tenant_id,
        )
        sql_user_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM dbo.IntegrityDecisionResults
            WHERE TenantId=? AND TrainingDispositionSource='SYNTHETIC_GROUND_TRUTH'
            """,
            tenant_id,
        )
        decision_count = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM dbo.Nominations n
            JOIN dbo.Users u ON u.UserId=n.NominatorId
            WHERE u.TenantId=?
            """,
            tenant_id,
        )
        nomination_count = int(cursor.fetchone()[0])
        if (sql_user_count, nomination_count, decision_count) != (401, 5_000, 5_000):
            raise RuntimeError(
                "Post-load SQL counts are not 401 users / 5,000 nominations / "
                f"5,000 decisions: {sql_user_count}/{nomination_count}/{decision_count}"
            )
        conn.commit()
        report("SQL corpus transaction committed")
    except Exception:
        conn.rollback()
        raise
    return CorpusResult(
        sql_user_count=sql_user_count,
        nomination_count=nomination_count,
        decision_count=decision_count,
        inserted_nomination_count=inserted,
        generation_run_id=generation_run_id,
        sql_user_ids_by_logical_id=user_ids,
        admin_sql_user_id=admin_user_id,
    )
