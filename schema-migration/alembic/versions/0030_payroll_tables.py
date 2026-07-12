"""Add payroll_providers, payroll_tokens, payroll_submissions; Tenants.payroll_provider_id

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-01

Schema
------
payroll_providers
    One row per configured payroll provider instance.  Two rows can share the
    same name (e.g. both "gusto") but differ in company_id_at_provider — one
    per tenant that uses that provider.  The name column is the type
    discriminator: the payroll broker switches on it to pick the right API
    client (Gusto, Workday, ADP, ...).

    company_id_at_provider — the identifier by which the payroll provider
    knows this company:
        Gusto   → company UUID returned by /v1/me
        Workday → tenant name (e.g. "acme_corp")
        ADP     → company code
    provider_config — JSON blob for provider-specific extras that cannot live
    in api_base_url (e.g. Workday per-tenant host URL).

payroll_tokens
    OAuth credentials for a provider instance.  Rotates on every token
    refresh (~2h for Gusto).  Keyed by provider_id (UNIQUE) — no tenant_id
    needed here because the tenant→provider link lives in Tenants.

payroll_submissions
    One row per payroll submission attempt.  Maps the external payroll
    provider's reference (e.g. Gusto payroll UUID) back to a nomination_id
    so the webhook handler can resolve the nomination from a Gusto callback.

Tenants.payroll_provider_id
    FK to payroll_providers.  NULL = no payroll configured for this tenant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── payroll_providers ────────────────────────────────────────────────────
    op.create_table(
        "payroll_providers",
        sa.Column("id",                    sa.Integer(),      primary_key=True, autoincrement=True),
        sa.Column("name",                  sa.String(50),     nullable=False),           # "gusto", "workday"
        sa.Column("display_name",          sa.String(100),    nullable=False),           # "Gusto – ACME Corp"
        sa.Column("company_id_at_provider", sa.String(100),   nullable=True),            # provider's company ref
        sa.Column("provider_config",       sa.Unicode(None),  nullable=True),            # NVARCHAR(MAX) JSON
        sa.Column("api_base_url",          sa.String(255),    nullable=True),            # NULL = use hardcoded default
        sa.Column("oauth_base_url",        sa.String(255),    nullable=True),
    )

    # ── payroll_tokens ───────────────────────────────────────────────────────
    op.create_table(
        "payroll_tokens",
        sa.Column("id",               sa.Integer(),  primary_key=True, autoincrement=True),
        sa.Column("provider_id",      sa.Integer(),  sa.ForeignKey("payroll_providers.id"), nullable=False),
        sa.Column("access_token",     sa.Text(),     nullable=False),
        sa.Column("refresh_token",    sa.Text(),     nullable=False),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at",       sa.DateTime(), nullable=False, server_default=sa.text("GETUTCDATE()")),
        sa.Column("updated_at",       sa.DateTime(), nullable=False, server_default=sa.text("GETUTCDATE()")),
        sa.UniqueConstraint("provider_id", name="uq_payroll_tokens_provider"),
    )

    # ── payroll_submissions ──────────────────────────────────────────────────
    op.create_table(
        "payroll_submissions",
        sa.Column("id",                   sa.Integer(),  primary_key=True, autoincrement=True),
        sa.Column("nomination_id",        sa.Integer(),  sa.ForeignKey("Nominations.NominationId"), nullable=False),
        sa.Column("provider_id",          sa.Integer(),  sa.ForeignKey("payroll_providers.id"), nullable=False),
        sa.Column("provider_payroll_ref", sa.String(100), nullable=True),   # Gusto payroll UUID / Workday ref
        sa.Column("status",               sa.String(50), nullable=False, server_default="submitted"),
        sa.Column("submitted_at",         sa.DateTime(), nullable=False, server_default=sa.text("GETUTCDATE()")),
        sa.Column("completed_at",         sa.DateTime(), nullable=True),
    )

    op.create_index(
        "ix_payroll_submissions_provider_ref",
        "payroll_submissions",
        ["provider_payroll_ref"],
    )

    # ── Tenants.payroll_provider_id ──────────────────────────────────────────
    op.add_column(
        "Tenants",
        sa.Column(
            "payroll_provider_id",
            sa.Integer(),
            sa.ForeignKey("payroll_providers.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("Tenants", "payroll_provider_id")
    op.drop_index("ix_payroll_submissions_provider_ref", table_name="payroll_submissions")
    op.drop_table("payroll_submissions")
    op.drop_table("payroll_tokens")
    op.drop_table("payroll_providers")
