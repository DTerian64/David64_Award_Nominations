/* ============================================================================
   2026-08-20_01_split_rejection_actor_backfill.sql
   ----------------------------------------------------------------------------
   Backfill for the RejectionActor split.

   Until 2026-08 both auto-reject paths in integrity-check/inference/handler.py wrote the
   same literal string:

       handler.py:91   Check A description quality gate  -> "Fraud Detection"
       handler.py:209  CRITICAL ML auto-reject           -> "Fraud Detection"

   modeling.train_rf_model.load_data() excludes Rejected + 'Fraud Detection' rows on
   the grounds that a failed description gate is not a fraud signal. Because the
   two paths shared a string, that exclusion also discarded every genuine ML
   auto-reject.

   The code change makes Check A write 'Fraud Detection (Description)' and the
   exclusion now filters on that value. This script renames the historical rows
   so old and new data mean the same thing.

   SAFETY
     - Wrapped in an explicit transaction. Review the PRE-CHECK output, then
       either COMMIT or ROLLBACK by hand.
     - Idempotent: re-running matches zero rows.
     - Guarded: only renames rows with no CRITICAL P2P score. Any row that IS
       CRITICAL cannot be attributed to Check A and is left alone for manual
       review. On tenant 1 that guard is expected to exclude 0 rows, because
       CRITICAL + Rejected currently has no members at all.

   RUN ORDER: this script, then deploy the integrity-check image. Running it
   before deployment is safe; running it after is also safe.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- @@ROWCOUNT is captured into a variable the statement after each DML, never
-- read later. PRINT is itself a statement and resets @@ROWCOUNT to 0, so
-- "PRINT 'header'; PRINT CAST(@@ROWCOUNT ...)" always reports 0 rows affected.
DECLARE @Rows INT;
DECLARE @OldActor NVARCHAR(256) = N'Fraud Detection';
DECLARE @NewActor NVARCHAR(256) = N'Fraud Detection (Description)';

/* ── PRE-CHECK ────────────────────────────────────────────────────────────── */
PRINT '--- BEFORE ---';

SELECT  n.RejectionActor,
        COUNT(*) AS Rows_
FROM    dbo.Nominations n
WHERE   n.Status = 'Rejected'
GROUP BY n.RejectionActor
ORDER BY Rows_ DESC;

-- Size of the training population under TODAY's rule, captured before the
-- rename so the post-check has something honest to compare against.
DECLARE @IncludedBefore INT;
SELECT  @IncludedBefore = COUNT(*)
FROM    dbo.Nominations n
JOIN    dbo.Users u ON u.UserId = n.NominatorId
WHERE   u.TenantId = 1
  AND   n.Status NOT IN ('PendingHRBPReview')
  AND   NOT (n.Status = 'Rejected' AND n.RejectionActor = @OldActor);

-- Rows the guard will refuse to touch. Expected: 0.
SELECT  n.NominationId,
        n.Status,
        n.RejectionActor,
        p.RiskLevel,
        p.FraudScore
FROM    dbo.Nominations n
JOIN    dbo.P2P_FraudScores p ON p.NominationId = n.NominationId
WHERE   n.Status         = 'Rejected'
  AND   n.RejectionActor = @OldActor
  AND   p.RiskLevel      = 'CRITICAL';

/* ── BACKFILL ─────────────────────────────────────────────────────────────── */
BEGIN TRANSACTION;

UPDATE  n
SET     n.RejectionActor = @NewActor,
        n.updated_at     = SYSUTCDATETIME(),
        n.updated_by     = N'backfill:rejection-actor-split'
FROM    dbo.Nominations n
WHERE   n.Status         = 'Rejected'
  AND   n.RejectionActor = @OldActor
  AND   NOT EXISTS (
            SELECT 1
            FROM   dbo.P2P_FraudScores p
            WHERE  p.NominationId = n.NominationId
              AND  p.RiskLevel    = 'CRITICAL'
        );

SET @Rows = @@ROWCOUNT;
PRINT '--- ROWS RENAMED: ' + CAST(@Rows AS VARCHAR(20)) + ' ---';

/* ── POST-CHECK ───────────────────────────────────────────────────────────── */
PRINT '--- AFTER ---';

SELECT  n.RejectionActor,
        COUNT(*) AS Rows_
FROM    dbo.Nominations n
WHERE   n.Status = 'Rejected'
GROUP BY n.RejectionActor
ORDER BY Rows_ DESC;

-- Training-population delta. Included_After must equal Included_Before: the
-- whole point of doing the rename and the exclusion change in one commit is
-- that today's label set does not move. If they differ, STOP and roll back.
DECLARE @IncludedAfter INT;
SELECT  @IncludedAfter = COUNT(*)
FROM    dbo.Nominations n
JOIN    dbo.Users u ON u.UserId = n.NominatorId
WHERE   u.TenantId = 1
  AND   n.Status NOT IN ('PendingHRBPReview')
  AND   NOT (n.Status = 'Rejected' AND n.RejectionActor = @NewActor);

SELECT  @IncludedBefore AS Included_Before,
        @IncludedAfter  AS Included_After,
        CASE WHEN @IncludedBefore = @IncludedAfter
             THEN 'OK - label set unchanged'
             ELSE 'MISMATCH - ROLLBACK' END AS Verdict;

/* Review the output above, then run exactly one of: */
-- COMMIT TRANSACTION;
-- ROLLBACK TRANSACTION;
