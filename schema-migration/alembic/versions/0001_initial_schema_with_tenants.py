"""Initial schema with Tenants table and TenantId on Users

Revision ID: 0001
Revises:
Create Date: 2026-03-14

Context
-------
This is the first Alembic-managed migration for the Award Nomination backend.

The production database was previously managed with SQLAlchemy ``create_all()``.
This migration documents the full baseline schema AND adds the multi-tenancy
changes in a single step:

  New table  : Tenants
  New column : Users.TenantId  (FK → Tenants.TenantId, NOT NULL)
  New index  : uq_users_upn_tenant  (UPN is unique per tenant, not globally)

The old Users.userPrincipalName UNIQUE constraint is dropped in favour of the
composite unique constraint.

Upgrade order
-------------
  1. Create Tenants table
  2. Create baseline tables that did not exist yet (FraudScores, etc.)
  3. Add TenantId column to Users as NULLABLE first
  4. Back-fill TenantId to 1 (seed migration assumes tenant 1 = Rideshare David64 Org)
  5. Alter column to NOT NULL
  6. Add FK constraint and composite unique constraint

Downgrade
---------
  Reverses the above in the opposite order.  The Tenants table is dropped and
  Users.TenantId is removed, restoring the original UNIQUE constraint on UPN.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = :t AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name},
    )
    return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


def _constraint_exists(conn, table_name: str, constraint_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
            "WHERE TABLE_NAME = :t AND CONSTRAINT_NAME = :cn AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "cn": constraint_name},
    )
    return result.fetchone() is not None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Tenants table ─────────────────────────────────────────────────
    if not _table_exists(conn, "Tenants"):
        op.create_table(
            "Tenants",
            sa.Column("TenantId",        sa.Integer(),     primary_key=True, autoincrement=True),
            sa.Column("TenantName",      sa.String(256),   nullable=False),
            sa.Column("AzureAdTenantId", sa.String(36),    nullable=False),
            sa.UniqueConstraint("TenantName",      name="uq_tenants_name"),
            sa.UniqueConstraint("AzureAdTenantId", name="uq_tenants_aad_id"),
        )

    # ── 2. Baseline tables (idempotent — skip if already present) ────────
    if not _table_exists(conn, "Users"):
        op.create_table(
            "Users",
            sa.Column("UserId",            sa.Integer(),    primary_key=True),
            sa.Column("userPrincipalName", sa.String(256),  nullable=False),
            sa.Column("userEmail",         sa.String(256),  nullable=True),
            sa.Column("FirstName",         sa.String(128),  nullable=True),
            sa.Column("LastName",          sa.String(128),  nullable=True),
            sa.Column("Title",             sa.String(256),  nullable=True),
            sa.Column("ManagerId",         sa.Integer(),    sa.ForeignKey("Users.UserId"), nullable=True),
            # TenantId added further below — added here only on fresh schema
            sa.Column("TenantId",          sa.Integer(),    sa.ForeignKey("Tenants.TenantId"), nullable=False, server_default="1"),
            sa.UniqueConstraint("userPrincipalName", "TenantId", name="uq_users_upn_tenant"),
        )

    if not _table_exists(conn, "Nominations"):
        op.create_table(
            "Nominations",
            sa.Column("NominationId",          sa.Integer(),    primary_key=True, autoincrement=True),
            sa.Column("NominatorId",           sa.Integer(),    sa.ForeignKey("Users.UserId"), nullable=False),
            sa.Column("BeneficiaryId",         sa.Integer(),    sa.ForeignKey("Users.UserId"), nullable=False),
            sa.Column("ApproverId",            sa.Integer(),    sa.ForeignKey("Users.UserId"), nullable=False),
            sa.Column("DollarAmount",          sa.Integer(),    nullable=False),
            sa.Column("NominationDescription", sa.String(500),  nullable=True),
            sa.Column("NominationDate",        sa.DateTime(),   server_default=sa.text("GETDATE()")),
            sa.Column("Status",                sa.String(50),   server_default="Pending"),
            sa.Column("ApprovedDate",          sa.DateTime(),   nullable=True),
            sa.Column("PayedDate",             sa.DateTime(),   nullable=True),
        )

    if not _table_exists(conn, "FraudScores"):
        op.create_table(
            "FraudScores",
            sa.Column("FraudScoreId", sa.Integer(),    primary_key=True, autoincrement=True),
            sa.Column("NominationId", sa.Integer(),    sa.ForeignKey("Nominations.NominationId"), nullable=False),
            sa.Column("FraudScore",   sa.Integer(),    nullable=False),
            sa.Column("RiskLevel",    sa.String(50),   nullable=False),
            sa.Column("FraudFlags",   sa.String(2000), nullable=True),
        )

    if not _table_exists(conn, "Impersonation_AuditLog"):
        op.create_table(
            "Impersonation_AuditLog",
            sa.Column("AuditId",         sa.Integer(),     primary_key=True, autoincrement=True),
            sa.Column("AdminUPN",        sa.String(256),   nullable=False),
            sa.Column("ImpersonatedUPN", sa.String(256),   nullable=False),
            sa.Column("Action",          sa.String(128),   nullable=False),
            sa.Column("Details",         sa.String(1000),  nullable=True),
            sa.Column("IpAddress",       sa.String(64),    nullable=True),
            sa.Column("Timestamp",       sa.DateTime(),    server_default=sa.text("GETDATE()")),
        )

    # ── 3–6. Add TenantId to an existing Users table ─────────────────────
    # (skipped when Users was just created above — it already has the column)
    if _table_exists(conn, "Users") and not _column_exists(conn, "Users", "TenantId"):

        # 3a. Drop the old global UPN unique constraint if it exists
        if _constraint_exists(conn, "Users", "UQ__Users__userPrincipalName"):
            op.drop_constraint("UQ__Users__userPrincipalName", "Users", type_="unique")
        # Also try the common auto-named variant SQL Server generates
        result = conn.execute(
            sa.text(
                "SELECT name FROM sys.indexes "
                "WHERE object_id = OBJECT_ID('dbo.Users') "
                "  AND is_unique = 1 "
                "  AND name LIKE '%userPrincipalName%'"
            )
        )
        for row in result.fetchall():
            op.drop_constraint(row[0], "Users", type_="unique")

        # 3b. Add TenantId as nullable first (SQL Server requires this before back-fill)
        op.add_column(
            "Users",
            sa.Column("TenantId", sa.Integer(), nullable=True),
        )

        # 4. Back-fill — assign all existing users to tenant 1
        #    (seed script will have already inserted tenant 1)
        conn.execute(sa.text("UPDATE dbo.Users SET TenantId = 1 WHERE TenantId IS NULL"))

        # 5. Alter to NOT NULL
        op.alter_column("Users", "TenantId", nullable=False)

        # 6a. Add FK
        op.create_foreign_key(
            "fk_users_tenant",
            "Users",
            "Tenants",
            ["TenantId"],
            ["TenantId"],
        )

        # 6b. Composite unique constraint (UPN unique within a tenant)
        op.create_unique_constraint(
            "uq_users_upn_tenant",
            "Users",
            ["userPrincipalName", "TenantId"],
        )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    conn = op.get_bind()

    if _column_exists(conn, "Users", "TenantId"):
        # Remove composite unique + FK
        if _constraint_exists(conn, "Users", "uq_users_upn_tenant"):
            op.drop_constraint("uq_users_upn_tenant", "Users", type_="unique")
        if _constraint_exists(conn, "Users", "fk_users_tenant"):
            op.drop_constraint("fk_users_tenant", "Users", type_="foreignkey")

        # Restore global unique on UPN
        op.create_unique_constraint(
            "uq_users_upn",
            "Users",
            ["userPrincipalName"],
        )

        op.drop_column("Users", "TenantId")

    if _table_exists(conn, "Tenants"):
        op.drop_table("Tenants")
