/* ############################################################################
   ##  HOLD - DO NOT RUN.  Decision 2026-08-20.
   ##
   ##  These scripts were written to raise the count of human-confirmed fraud
   ##  labels, on the reasoning that an HRBP verdict is the only ground truth
   ##  worth training against. Under the ELCE research programme (NSF SBIR
   ##  Project Pitch v6.0) that reasoning does not hold for tenant 1:
   ##
   ##    "exact optima are computable only when the generating mechanism is
   ##     known"                                    -- Pitch, Objective 4
   ##
   ##  Tenant 1 is synthetic. An HRBP reviewing it is guessing about data a
   ##  script already knows the answer to. The ground truth that matters is the
   ##  GENERATOR's provenance, not a human's opinion, and that is a different
   ##  piece of work (persist scheme membership per nomination).
   ##
   ##  Script _02_ is additionally counter-productive here: load_data() excludes
   ##  PendingHRBPReview, so moving 189 rows REMOVES 189 composite decisions from
   ##  the corpus ELCE needs to explain.
   ##
   ##  Script _01_ (the RejectionActor split) is NOT held - it is a prerequisite
   ##  for building the event-lineage graph at all, because the lineage graph
   ##  terminates in a decision node and the two auto-reject paths are currently
   ##  indistinguishable in the stored outcome.
   ##
   ##  Revisit if: a real tenant needs labels, or if human adjudication becomes
   ##  the subject of an experiment rather than a source of ground truth.
   ############################################################################ */

/* ============================================================================
   2026-08-20_02_route_critical_pending_to_hrbp.sql
   ----------------------------------------------------------------------------
   Route CRITICAL-scored, still-Pending nominations into the HRBP review queue
   so a human produces real ground-truth labels.

   WHY THIS MATTERS MORE THAN IT LOOKS
     dbo.P2P_FraudScores has zero rows with ConfirmedBy IS NOT NULL. Every label
     both models train on today is the Random Forest's own prior output, or the
     unlabelled -> 0 convention. labels.py will report n_hrbp = 0 and warn that
     the human-label evaluation cannot be performed. This script is how that
     number stops being zero.

   WHAT IT DOES
     1. Selects a stratified tranche of tenant-1 nominations that are
        Status='Pending' and scored CRITICAL.
     2. Ensures each has a dbo.HRBP_FraudFlags row, so get_hrbp_queue() shows
        the reviewer a score, flags and SHAP breakdown rather than a bare row.
     3. Moves them to Status='PendingHRBPReview'.

   SIDE EFFECT ON TRAINING — READ THIS
     load_data() excludes PendingHRBPReview. Every nomination this script moves
     LEAVES the Random Forest's training population until it is adjudicated.
     That is the intended trade: rows currently labelled IsFraud=1 purely on the
     model's own say-so are withdrawn, and come back carrying a human verdict.
     But if the queue is never worked, the rows are simply gone. Do not run this
     unless somebody is actually going to review them.

   ON BATCH SIZE
     All 189 in one go is a large queue for one reviewer, and a reviewer working
     through a long homogeneous list gets less careful, not more. @BatchSize
     defaults to 40, drawn evenly across four score quartiles so the reviewer
     sees the full range and can calibrate rather than rubber-stamping the top.
     Re-run to take the next tranche; already-moved rows are not re-selected.

   SAFETY
     - Explicit transaction; review output, then COMMIT or ROLLBACK by hand.
     - Idempotent: rows already in PendingHRBPReview are not matched.
     - Touches only Status, not Amount, not payment state.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- @@ROWCOUNT is captured into a variable the statement after each DML, never
-- read later. PRINT is itself a statement and resets @@ROWCOUNT to 0, so
-- "PRINT 'header'; PRINT CAST(@@ROWCOUNT ...)" always reports 0 rows affected.
DECLARE @Rows INT;
DECLARE @TenantId  INT = 1;
DECLARE @BatchSize INT = 40;      -- set to 189 to move the whole cohort

/* ── PRE-CHECK ────────────────────────────────────────────────────────────── */
PRINT '--- CRITICAL cohort by status, before ---';

SELECT  n.Status,
        COUNT(*)          AS Rows_,
        MIN(p.FraudScore) AS MinScore,
        MAX(p.FraudScore) AS MaxScore
FROM    dbo.Nominations n
JOIN    dbo.Users u            ON u.UserId       = n.NominatorId
JOIN    dbo.P2P_FraudScores p  ON p.NominationId = n.NominationId
WHERE   u.TenantId  = @TenantId
  AND   p.RiskLevel = 'CRITICAL'
GROUP BY n.Status
ORDER BY Rows_ DESC;

/* ── SELECT THE TRANCHE ───────────────────────────────────────────────────── */
DROP TABLE IF EXISTS #Tranche;

-- Two CTEs, not one: T-SQL will not accept a window function inside another
-- window function's PARTITION BY, so the quartile has to be materialised first.
WITH cohort AS (
    SELECT  n.NominationId,
            p.FraudScore,
            p.FraudFlags,
            NTILE(4) OVER (ORDER BY p.FraudScore DESC, n.NominationId) AS Quartile
    FROM    dbo.Nominations n
    JOIN    dbo.Users u           ON u.UserId       = n.NominatorId
    JOIN    dbo.P2P_FraudScores p ON p.NominationId = n.NominationId
    WHERE   u.TenantId  = @TenantId
      AND   n.Status    = 'Pending'
      AND   p.RiskLevel = 'CRITICAL'
),
ranked AS (
    SELECT  c.*,
            ROW_NUMBER() OVER (PARTITION BY c.Quartile ORDER BY NEWID()) AS rn
    FROM    cohort c
)
SELECT NominationId, FraudScore, FraudFlags, Quartile
INTO   #Tranche
FROM   ranked
WHERE  rn <= CEILING(@BatchSize / 4.0);

PRINT '--- TRANCHE SELECTED (by quartile) ---';
SELECT Quartile, COUNT(*) AS Rows_, MIN(FraudScore) AS MinScore, MAX(FraudScore) AS MaxScore
FROM   #Tranche GROUP BY Quartile ORDER BY Quartile;

BEGIN TRANSACTION;

/* ── 1. Give the reviewer context ─────────────────────────────────────────── */
-- HRBP_FraudFlags.FraudProbability is NOT NULL and P2P_FraudScores has no
-- probability column, so it is derived from the 0-100 score. That is a display
-- value for the reviewer, not a model output — no training path reads it.
INSERT INTO dbo.HRBP_FraudFlags
        (NominationId, FraudScore, FraudProbability, RiskLevel,
         WarningFlags, TopFeaturesJson, FeatureSummaryJson, CreatedAt)
SELECT  t.NominationId,
        t.FraudScore,
        CAST(t.FraudScore AS FLOAT) / 100.0,
        'CRITICAL',
        COALESCE(t.FraudFlags, 'Backfilled from P2P_FraudScores; no flags recorded at scoring time'),
        NULL,          -- no SHAP snapshot: these were scored by the weekly
        NULL,          -- score_and_save_historical() pass, which does not persist one
        GETUTCDATE()
FROM    #Tranche t
WHERE   NOT EXISTS (
            SELECT 1 FROM dbo.HRBP_FraudFlags f
            WHERE  f.NominationId = t.NominationId
        );

SET @Rows = @@ROWCOUNT;
PRINT '--- HRBP_FraudFlags rows inserted: ' + CAST(@Rows AS VARCHAR(20)) + ' ---';

/* ── 2. Move to the queue ─────────────────────────────────────────────────── */
UPDATE  n
SET     n.Status     = 'PendingHRBPReview',
        n.updated_at = SYSUTCDATETIME(),
        n.updated_by = N'backfill:critical-to-hrbp'
FROM    dbo.Nominations n
JOIN    #Tranche t ON t.NominationId = n.NominationId
WHERE   n.Status = 'Pending';

SET @Rows = @@ROWCOUNT;
PRINT '--- Nominations moved to PendingHRBPReview: ' + CAST(@Rows AS VARCHAR(20)) + ' ---';

/* ── POST-CHECK ───────────────────────────────────────────────────────────── */
PRINT '--- CRITICAL cohort by status, after ---';

SELECT  n.Status, COUNT(*) AS Rows_
FROM    dbo.Nominations n
JOIN    dbo.Users u           ON u.UserId       = n.NominatorId
JOIN    dbo.P2P_FraudScores p ON p.NominationId = n.NominationId
WHERE   u.TenantId  = @TenantId
  AND   p.RiskLevel = 'CRITICAL'
GROUP BY n.Status
ORDER BY Rows_ DESC;

-- Every queued row must have flags, or the reviewer sees a blank card.
SELECT  COUNT(*) AS Queued_Without_Flags
FROM    dbo.Nominations n
JOIN    dbo.Users u ON u.UserId = n.NominatorId
LEFT JOIN dbo.HRBP_FraudFlags f ON f.NominationId = n.NominationId
WHERE   u.TenantId = @TenantId
  AND   n.Status   = 'PendingHRBPReview'
  AND   f.FlagId IS NULL;

/* Review the output above, then run exactly one of: */
-- COMMIT TRANSACTION;
-- ROLLBACK TRANSACTION;

DROP TABLE IF EXISTS #Tranche;
