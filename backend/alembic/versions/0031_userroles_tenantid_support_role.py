"""Add TenantId to dbo.UserRoles; seed payroll_failed email template

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-02

Changes
-------
1. dbo.UserRoles — add TenantId INT column
   TenantId is denormalised from dbo.Users for query convenience (avoids
   a join when looking up "all Support users for tenant X").  It is
   backfilled from dbo.Users at migration time and kept in sync by the
   application on insert (the admin UI / API always sets it).

2. dbo.EmailTemplates — seed 'payroll_failed' default template (TenantId=1, Lang='en')
   Subject and HTML body for the support notification email sent when the
   payroll broker fails to submit an off-cycle payroll to the provider.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t AND COLUMN_NAME = :c"
    ), {"t": table_name, "c": column_name}).fetchone() is not None


def _template_exists(conn, key: str, tenant_id: int, lang: str) -> bool:
    return conn.execute(sa.text(
        "SELECT 1 FROM dbo.EmailTemplates "
        "WHERE TemplateKey = :k AND TenantId = :tid AND Lang = :lang"
    ), {"k": key, "tid": tenant_id, "lang": lang}).fetchone() is not None


_PAYROLL_FAILED_BODY = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <style>
    body      { font-family: Arial, sans-serif; font-size: 14px; color: #1a1a1a; margin: 0; padding: 0; }
    .wrapper  { max-width: 600px; margin: 32px auto; background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
    .header   { background: #b91c1c; padding: 20px 28px; }
    .header h1 { margin: 0; color: #fff; font-size: 18px; }
    .body     { padding: 28px; }
    .field    { margin-bottom: 12px; }
    .label    { font-weight: bold; color: #555; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
    .value    { margin-top: 2px; }
    .error-box { background: #fef2f2; border: 1px solid #fca5a5; border-radius: 4px; padding: 12px 16px; margin-top: 16px; font-family: monospace; font-size: 12px; color: #7f1d1d; word-break: break-all; }
    .footer   { padding: 16px 28px; background: #f9fafb; border-top: 1px solid #e5e7eb; font-size: 12px; color: #9ca3af; }
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>⚠ Payroll Submission Failed — Action Required</h1>
    </div>
    <div class="body">
      <p>The payroll broker was unable to submit an off-cycle payroll for the following nomination.
         Please investigate and reprocess or escalate as appropriate.</p>

      <div class="field">
        <div class="label">Nomination ID</div>
        <div class="value">#{{ nomination_id }}</div>
      </div>
      <div class="field">
        <div class="label">Beneficiary</div>
        <div class="value">{{ beneficiary_name }}</div>
      </div>
      <div class="field">
        <div class="label">Nominator</div>
        <div class="value">{{ nominator_name }}</div>
      </div>
      <div class="field">
        <div class="label">Amount</div>
        <div class="value">{{ formatted_amount }}</div>
      </div>

      <p style="margin-top: 20px; font-weight: bold; color: #b91c1c;">Provider error:</p>
      <div class="error-box">{{ error_message }}</div>

      <p style="margin-top: 24px;">
        Log in to the Award Nomination admin portal to view the full event trace for this nomination.
      </p>
    </div>
    <div class="footer">
      This is an automated alert from the Award Nomination payroll integration.
    </div>
  </div>
</body>
</html>
"""


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Add TenantId to dbo.UserRoles ─────────────────────────────────────
    if not _column_exists(conn, "UserRoles", "TenantId"):
        op.add_column(
            "UserRoles",
            sa.Column("TenantId", sa.Integer(), nullable=True),
            schema="dbo",
        )

        # Backfill from dbo.Users (every UserRoles row has a UserId FK → Users)
        conn.execute(sa.text("""
            UPDATE ur
            SET    ur.TenantId = u.TenantId
            FROM   dbo.UserRoles ur
            JOIN   dbo.Users u ON u.UserId = ur.UserId
            WHERE  ur.TenantId IS NULL
        """))

        # Now safe to enforce NOT NULL
        conn.execute(sa.text(
            "ALTER TABLE dbo.UserRoles ALTER COLUMN TenantId INT NOT NULL"
        ))

        # Index for the common lookup pattern: Role + TenantId
        conn.execute(sa.text(
            "CREATE INDEX ix_userroles_tenantid "
            "ON dbo.UserRoles (TenantId, Role)"
        ))

    # ── 2. Seed payroll_failed email template ─────────────────────────────────
    if not _template_exists(conn, "payroll_failed", 1, "en"):
        conn.execute(sa.text("""
            INSERT INTO dbo.EmailTemplates
                (TenantId, TemplateKey, Lang, Subject, BodyTemplate, Active, Version, UpdatedAt, UpdatedBy)
            VALUES
                (1, 'payroll_failed', 'en',
                 'Payroll Failed — Nomination #{{ nomination_id }}',
                 :body,
                 1, 1, SYSUTCDATETIME(), 'migration-0031')
        """), {"body": _PAYROLL_FAILED_BODY})


def downgrade() -> None:
    conn = op.get_bind()

    # Remove template seed
    conn.execute(sa.text(
        "DELETE FROM dbo.EmailTemplates "
        "WHERE TemplateKey = 'payroll_failed' AND TenantId = 1 AND UpdatedBy = 'migration-0031'"
    ))

    # Drop index and column
    if _column_exists(conn, "UserRoles", "TenantId"):
        conn.execute(sa.text("DROP INDEX IF EXISTS ix_userroles_tenantid ON dbo.UserRoles"))
        op.drop_column("UserRoles", "TenantId", schema="dbo")
