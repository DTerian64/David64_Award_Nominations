"""Backfill min_award / max_award into dbo.Tenants.Config

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-09

Context
-------
Adds ``min_award`` and ``max_award`` numeric fields to the per-tenant Config
JSON column.  These control the minimum and maximum award amounts that
nominators are allowed to enter, both on the frontend (UI validation) and the
backend (API-layer enforcement in nominations_router.py).

The migration uses SQL Server's ``JSON_MODIFY`` to patch the existing JSON in
place without touching any other fields.  Only rows where Config is not NULL
and the field does not already exist are updated, making this idempotent.

NULL Config rows are left untouched — the application falls back to its own
defaults (50 / 5000) when the column is NULL.

Default values
--------------
* min_award: 50
* max_award: 5000

These match the previous hardcoded constants in the frontend.  Admins can
update them per-tenant via the admin API (PUT /api/admin/tenants/<id>/config).

Downgrade
---------
Removes the two fields from any Config rows where they were added by this
migration (i.e. rows where the stored values still match the defaults).  Rows
that have been subsequently edited by admins are left untouched to avoid data
loss.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Patch Config rows that already have JSON but are missing min_award and/or max_award.
    # JSON_MODIFY is idempotent — if the key already exists it overwrites; if not it inserts.
    # We only touch rows where at least one field is absent so we don't stomp on custom values.
    conn.execute(sa.text(
        """
        UPDATE dbo.Tenants
        SET Config = JSON_MODIFY(
                        JSON_MODIFY(Config, '$.min_award', CAST(50 AS INT)),
                        '$.max_award', CAST(5000 AS INT)
                     )
        WHERE Config IS NOT NULL
          AND (
                JSON_VALUE(Config, '$.min_award') IS NULL
             OR JSON_VALUE(Config, '$.max_award') IS NULL
              )
        """
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # Remove the fields from rows where both values still match the migration defaults.
    # Rows where an admin has changed the values are left alone.
    # SQL Server 2016+: passing NULL to JSON_MODIFY with lax path removes the key.
    conn.execute(sa.text(
        """
        UPDATE dbo.Tenants
        SET Config = JSON_MODIFY(
                        JSON_MODIFY(Config, 'lax $.min_award', NULL),
                        'lax $.max_award', NULL
                     )
        WHERE Config IS NOT NULL
          AND JSON_VALUE(Config, '$.min_award') = '50'
          AND JSON_VALUE(Config, '$.max_award') = '5000'
        """
    ))
