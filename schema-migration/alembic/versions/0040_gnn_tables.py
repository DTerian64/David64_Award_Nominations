"""Adopt HRBP confirmation columns; create GNN embeddings and score tables.

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-14

Context
-------
This revision does two things. They are unrelated in subject but both are
prerequisites for reliable GNN training and evaluation, and neither has been
applied to any environment through the migration chain.

PART 1 — adopt the HRBP confirmation columns into the chain
-----------------------------------------------------------
backend/utils/sqlhelper2.py MERGEs HRBP review decisions into
dbo.P2P_FraudScores.IsFraud, .ConfirmedBy and .ConfirmedAt. No revision through
0039 ever created those three columns: the original MERGE failed, and the
columns were added by hand directly against the database.

The consequence is that the migration chain no longer describes the schema. A
brand-new environment stood up from Terraform + `alembic upgrade head` — which
is exactly how production is meant to be built under ADR-0001 — would come up
WITHOUT these columns, and the HRBP confirmation MERGE would fail there in the
same way it originally failed.

This part is therefore a no-op on every existing environment (the columns are
already present and each ALTER is guarded) and a correctness fix on any
environment built from scratch. It restores the ADR-0001 rule that all DDL flows
through schema-migration.

Why it matters beyond compliance: ConfirmedBy is the only thing in the schema
that distinguishes a human fraud label from one the Random Forest wrote about
itself. train_fraud_model.load_data() currently reads RiskLevel and cannot tell
them apart. Reliable model-quality claims and GNN evaluation against human
labels depend on that distinction being available.

PART 2 — the GNN tables
------------------------
dbo.GNN_UserEmbeddings — one row per (TenantId, UserId, AsOfDate). The weekly
  job APPENDS a new snapshot rather than overwriting, mirroring dbo.UserGraphFlags
  from migration 0028, so a score can always be reproduced from the embeddings
  that actually produced it.

  ModelVersion must match the decoder artifact (gnn_head_tenant_<id>.pt). A
  mismatch means embeddings and decoder came from different training runs;
  integrity-check suppresses the score rather than emitting a confident wrong
  one. That guard is the reason the column exists.

dbo.GNN_FraudScores — one row per (NominationId, ModelVersion). Parallel in
  shape to dbo.P2P_FraudScores (migration 0016) with four provenance columns
  added: ModelVersion, EmbeddingAsOfDate, ScoringMode, ScoredBy. Together they
  answer, months later, "which model produced this score, how stale were its
  inputs, was it allowed to affect routing, and which service wrote it" —
  required for model governance and the SOC 2 evidence trail.

GNN score history design
------------------------
A single-column UNIQUE (NominationId), matching P2P_FraudScores, would make
shadow-mode exit criterion 4 (week-over-week score drift for unchanged
nominations) unmeasurable, because each weekly rescore would overwrite the
previous value and no history would survive.

The constraint is therefore UNIQUE (NominationId, ModelVersion). ModelVersion
changes on every retrain, so this yields exactly one row per nomination per
training run — bounded, and directly measurable for drift.

Asymmetric downgrade — deliberate
----------------------------------
downgrade() drops the two GNN tables but does NOT drop the three
P2P_FraudScores columns.

Dropping them would break the live backend HRBP confirmation MERGE and destroy
the only human-labelled fraud data in the system. This revision adopts columns
that already exist in every deployed environment rather than creating them, so
reversing that adoption is not a safe inverse. Downgrading to 0039 leaves the
schema exactly as it was before this revision ran.

Indexing note
-------------
Migration 0028 pairs its point-in-time PK with a redundant IX_*_PointInTime
nonclustered index. That is not repeated here. The clustered PK
(TenantId, UserId, AsOfDate) already serves "latest snapshot for this user" as a
seek plus a single-row backward scan. Duplicating it would be particularly
expensive on this table because Embedding is VARBINARY(MAX) — a covering index
would double the stored embedding data for no gain.
"""

import sqlalchemy as sa
from alembic import op

revision      = "0040"
down_revision = "0039"
branch_labels = None
depends_on    = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    return conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t"
        ),
        {"t": name},
    ).fetchone() is not None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    return conn.execute(
        sa.text(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = :t AND COLUMN_NAME = :c"
        ),
        {"t": table, "c": column},
    ).fetchone() is not None


def _index_exists(table: str, index: str) -> bool:
    conn = op.get_bind()
    return conn.execute(
        sa.text("SELECT 1 FROM sys.indexes WHERE name = :n AND object_id = OBJECT_ID(:t)"),
        {"n": index, "t": f"dbo.{table}"},
    ).fetchone() is not None


def upgrade() -> None:
    # ═══════════════════════════════════════════════════════════════════════════
    # PART 1 — adopt the hand-applied HRBP confirmation columns
    # ═══════════════════════════════════════════════════════════════════════════
    # Guarded individually: a no-op where they were applied by hand, a fix on a
    # freshly built environment. Each ALTER is its own statement because SQL
    # Server will not add multiple columns with differing nullability in one
    # ADD clause reliably across versions.

    if _table_exists("P2P_FraudScores"):
        if not _column_exists("P2P_FraudScores", "IsFraud"):
            # NULL means "never reviewed by a human". Only the HRBP confirmation
            # path in backend/utils/sqlhelper2.py sets it, so rows written by RF
            # scoring stay NULL — which is the distinction the label work needs.
            op.execute("ALTER TABLE dbo.P2P_FraudScores ADD IsFraud BIT NULL;")

        if not _column_exists("P2P_FraudScores", "ConfirmedBy"):
            # NVARCHAR(256) matches the created_by / updated_by convention
            # established for the audit quartet in migration 0034.
            op.execute("ALTER TABLE dbo.P2P_FraudScores ADD ConfirmedBy NVARCHAR(256) NULL;")

        if not _column_exists("P2P_FraudScores", "ConfirmedAt"):
            op.execute("ALTER TABLE dbo.P2P_FraudScores ADD ConfirmedAt DATETIME2 NULL;")

        # Filtered index for the label query that both models will run:
        #   WHERE ConfirmedBy IS NOT NULL  -> human ground truth only.
        # Filtered because confirmed rows are a small minority of the table.
        if not _index_exists("P2P_FraudScores", "IX_P2P_FraudScores_Confirmed"):
            op.execute("""
                CREATE INDEX IX_P2P_FraudScores_Confirmed
                    ON dbo.P2P_FraudScores (ConfirmedAt)
                    INCLUDE (NominationId, IsFraud, FraudScore, RiskLevel)
                    WHERE ConfirmedBy IS NOT NULL;
            """)

    # ═══════════════════════════════════════════════════════════════════════════
    # PART 2 — GNN tables
    # ═══════════════════════════════════════════════════════════════════════════

    if not _table_exists("GNN_UserEmbeddings"):
        op.execute("""
            CREATE TABLE dbo.GNN_UserEmbeddings (
                TenantId       INT            NOT NULL,
                UserId         INT            NOT NULL,
                AsOfDate       DATE           NOT NULL,
                -- float32 vector, same byte encoding as NomGraph_NominationEmbedding.
                -- VARBINARY(MAX) rather than a fixed width so the embedding
                -- dimension can change without a schema migration.
                Embedding      VARBINARY(MAX) NOT NULL,
                EmbeddingDim   SMALLINT       NOT NULL,
                -- Must equal the ModelVersion inside gnn_head_tenant_<id>.pt.
                ModelVersion   VARCHAR(64)    NOT NULL,
                LastUpdatedUtc DATETIME2      NOT NULL
                    CONSTRAINT DF_GNN_UserEmbeddings_LastUpdatedUtc DEFAULT SYSUTCDATETIME(),
                CONSTRAINT PK_GNN_UserEmbeddings
                    PRIMARY KEY CLUSTERED (TenantId, UserId, AsOfDate),
                CONSTRAINT CK_GNN_UserEmbeddings_EmbeddingDim
                    CHECK (EmbeddingDim > 0)
            );
        """)

        # Retention sweep: "delete everything for this tenant older than X".
        # Not served by the clustered PK, whose column order puts AsOfDate last.
        op.execute("""
            CREATE INDEX IX_GNN_UserEmbeddings_Retention
                ON dbo.GNN_UserEmbeddings (TenantId, AsOfDate);
        """)

    if not _table_exists("GNN_FraudScores"):
        op.execute("""
            CREATE TABLE dbo.GNN_FraudScores (
                GNNScoreId        INT            IDENTITY(1,1) NOT NULL,
                NominationId      INT            NOT NULL,
                FraudScore        INT            NOT NULL,   -- 0-100
                FraudProbability  FLOAT          NOT NULL,   -- 0.0-1.0
                RiskLevel         VARCHAR(20)    NOT NULL,   -- NONE/LOW/MEDIUM/HIGH/CRITICAL/UNKNOWN
                FraudFlags        NVARCHAR(500)  NULL,       -- comma-separated warning flags

                -- ── Provenance ───────────────────────────────────────────────
                -- Which decoder produced this score.
                ModelVersion      VARCHAR(64)    NOT NULL,
                -- How stale the node embeddings were when this score was computed.
                EmbeddingAsOfDate DATE           NULL,
                -- Whether this score was permitted to influence routing.
                ScoringMode       VARCHAR(10)    NOT NULL
                    CONSTRAINT DF_GNN_FraudScores_ScoringMode DEFAULT 'shadow',
                -- Service marker, e.g. svc:fraud-analytics-job (batch backfill)
                -- or svc:integrity-check (live submission). Distinguishes the two
                -- producers, which P2P_FraudScores cannot do.
                ScoredBy          NVARCHAR(256)  NULL,

                CreatedAt         DATETIME2      NOT NULL
                    CONSTRAINT DF_GNN_FraudScores_CreatedAt DEFAULT SYSUTCDATETIME(),

                CONSTRAINT PK_GNN_FraudScores PRIMARY KEY CLUSTERED (GNNScoreId),
                CONSTRAINT FK_GNN_FraudScores_Nominations
                    FOREIGN KEY (NominationId) REFERENCES dbo.Nominations (NominationId),
                -- One row per nomination per training run. See "Deviation" above.
                CONSTRAINT UQ_GNN_FraudScores_Nomination_Version
                    UNIQUE (NominationId, ModelVersion),
                CONSTRAINT CK_GNN_FraudScores_ScoringMode
                    CHECK (ScoringMode IN ('shadow', 'active')),
                CONSTRAINT CK_GNN_FraudScores_FraudScore
                    CHECK (FraudScore BETWEEN 0 AND 100),
                CONSTRAINT CK_GNN_FraudScores_FraudProbability
                    CHECK (FraudProbability BETWEEN 0.0 AND 1.0)
            );
        """)

        # Shadow-mode evaluation: risk-level distribution and GNN-vs-RF agreement.
        op.execute("""
            CREATE INDEX IX_GNN_FraudScores_RiskLevel
                ON dbo.GNN_FraudScores (RiskLevel);
        """)

        # "Latest score for this nomination" and per-nomination drift history.
        op.execute("""
            CREATE INDEX IX_GNN_FraudScores_Nomination
                ON dbo.GNN_FraudScores (NominationId, CreatedAt DESC)
                INCLUDE (FraudScore, RiskLevel, ScoringMode, ModelVersion);
        """)


def downgrade() -> None:
    # Drops PART 2 only. See "Asymmetric downgrade" in the module docstring:
    # the P2P_FraudScores columns are adopted by this revision, not created by
    # it, and dropping them would break the live HRBP confirmation MERGE and
    # destroy the only human-labelled fraud data in the system.
    if _table_exists("GNN_FraudScores"):
        op.execute("DROP TABLE dbo.GNN_FraudScores;")
    if _table_exists("GNN_UserEmbeddings"):
        op.execute("DROP TABLE dbo.GNN_UserEmbeddings;")
