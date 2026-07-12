"""Create NomGraph_NominationEmbedding table for caching copy-paste detector embeddings

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-16

Context
-------
detect_copy_paste() in the fraud analytics job encodes every eligible
nomination description into a sentence-transformer embedding on every weekly
run.  Approved/Paid nominations are immutable — their text never changes —
so re-encoding them on every run is pure wasted compute and RAM.

This table caches the 384-dimensional float32 embedding vector for each
nomination that has been encoded.  On subsequent runs the detector:

  1. Loads cached vectors for all eligible NominationIds in one batch SELECT.
  2. Encodes only the delta — nominations with no cached row (typically
     one week's worth of new approvals at steady state).
  3. INSERTs the fresh vectors so they are cached for future runs.
  4. Builds the full embedding matrix from cached + new vectors and runs
     the chunked cosine-similarity union-find as before.

A periodic eviction step (run once per job execution) deletes rows whose
NominationId is no longer within the active detection window, keeping the
table bounded to roughly DETECTION_WINDOW_DAYS × approval_rate rows.

Storage: 384 float32 × 4 bytes = 1 536 bytes per embedding.
At 50 000 cached nominations: ~75 MB — well within Azure SQL capacity.

VARBINARY(MAX) is used instead of a fixed size so the table accommodates
larger embedding models in the future without a schema migration.
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(sa.text("""
        SELECT 1 FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = 'dbo'
          AND TABLE_NAME   = :t
    """), {"t": table}).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "NomGraph_NominationEmbedding"):
        conn.execute(sa.text("""
            CREATE TABLE dbo.NomGraph_NominationEmbedding (
                NominationId  INT            NOT NULL,
                Embedding     VARBINARY(MAX) NOT NULL,
                EmbeddedAt    DATETIME2      NOT NULL DEFAULT GETUTCDATE(),
                CONSTRAINT PK_NomGraph_NominationEmbedding
                    PRIMARY KEY (NominationId)
            )
        """))


def downgrade() -> None:
    conn = op.get_bind()

    if _table_exists(conn, "NomGraph_NominationEmbedding"):
        conn.execute(sa.text("DROP TABLE dbo.NomGraph_NominationEmbedding"))
