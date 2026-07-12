"""Add desc_check_config to dbo.Tenants

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-06

Context
-------
Stores per-tenant description quality check configuration as a JSON blob.
Controls thresholds for the two pipeline checks in integrity-check:

  Check A (auto-reject):
    category_alignment_threshold  — minimum cosine similarity between
        description and category_description embeddings.  Nominations
        scoring below this are rejected before the ML model runs.
        Set to 0.0 to disable (e.g. tenants with no categories).

  Check B (HRBP flag):
    duplicate_similarity_threshold — cosine similarity above which a
        description is considered near-duplicate to the nominator's own
        prior descriptions.  Triggers a warning flag passed to HRBP review
        rather than an outright rejection (team nominations are legitimate).

  Language / model settings:
    embed_model       — sentence-transformer model name.  Defaults to
        'all-MiniLM-L6-v2' (English-optimised).  Set to
        'paraphrase-multilingual-MiniLM-L12-v2' for non-English tenants.
    use_char_count    — if true, length gate uses character count instead
        of word count (appropriate for CJK languages where a single
        character carries full-word meaning).
    min_char_count    — minimum characters (used when use_char_count=true).
    min_word_count    — minimum words   (used when use_char_count=false).

  Boilerplate:
    boilerplate_phrases — list of exact lowercased phrases that trigger
        an API-layer 422 rejection.  Language-specific.

NULL means "use all defaults" — no entry is required for English tenants
that are happy with the baseline thresholds.

Example value for a Korean tenant:
{
  "embed_model": "paraphrase-multilingual-MiniLM-L12-v2",
  "use_char_count": true,
  "min_char_count": 12,
  "category_alignment_threshold": 0.12,
  "duplicate_similarity_threshold": 0.85,
  "boilerplate_phrases": ["수고하셨습니다", "항상 최선을 다합니다"]
}

Downgrade
---------
Drops the column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c AND TABLE_SCHEMA = 'dbo'"
        ),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "Tenants", "desc_check_config"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants "
            "ADD desc_check_config NVARCHAR(MAX) NULL"
        ))


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "Tenants", "desc_check_config"):
        conn.execute(sa.text(
            "ALTER TABLE dbo.Tenants DROP COLUMN desc_check_config"
        ))
