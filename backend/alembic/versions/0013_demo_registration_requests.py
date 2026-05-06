"""Add DemoRegistrationRequests table for B2B invitation audit trail

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-06

Context
-------
Supports the demo self-registration flow where external visitors request
access to demo-awards.terian-services.com.

Flow
----
  1. Visitor submits name + email on /demo/request
  2. Backend calls Graph POST /invitations → Microsoft sends B2B invite email
  3. A row is inserted here for audit / dedup / rate-limit lookups
  4. Visitor accepts the invite → lands on /demo/welcome → signs in

Columns
-------
  Id             — autoincrement PK
  FirstName      — as provided by the visitor
  LastName       — as provided by the visitor
  Email          — the invited email address (indexed for dedup checks)
  IsAdmin        — whether AWard_Nomination_Admin role was requested
  AadObjectId    — guest object ID returned by Graph /invitations (nullable
                   until the invite API responds successfully)
  RequestIp      — IP address of the request (for rate limiting / audit)
  RequestedAt    — UTC timestamp of the invitation request

Downgrade
---------
  Drops the table entirely.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0013"
down_revision = "0012"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    exists = conn.execute(sa.text(
        "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'DemoRegistrationRequests'"
    )).fetchone()

    if exists:
        return

    op.create_table(
        "DemoRegistrationRequests",
        sa.Column("Id",          sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("FirstName",   sa.Unicode(128),  nullable=False),
        sa.Column("LastName",    sa.Unicode(128),  nullable=False),
        sa.Column("Email",       sa.String(256),   nullable=False),
        sa.Column("IsAdmin",     sa.Boolean(),     nullable=False, server_default="0"),
        sa.Column("AadObjectId", sa.String(36),    nullable=True),
        sa.Column("RequestIp",   sa.String(64),    nullable=True),
        sa.Column("RequestedAt", sa.DateTime(),    server_default=sa.text("GETUTCDATE()")),
        schema="dbo",
    )

    # Index on Email for fast dedup / rate-limit lookups
    conn.execute(sa.text(
        "CREATE INDEX ix_demoreg_email "
        "ON dbo.DemoRegistrationRequests (Email)"
    ))

    conn.execute(sa.text(
        "CREATE INDEX ix_demoreg_ip_time "
        "ON dbo.DemoRegistrationRequests (RequestIp, RequestedAt)"
    ))


def downgrade() -> None:
    op.drop_table("DemoRegistrationRequests", schema="dbo")
