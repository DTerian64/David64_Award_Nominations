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
   2026-08-20_03_critical_paid_adjudication.sql
   ----------------------------------------------------------------------------
   Ground-truth labels for the 565 CRITICAL nominations that were already Paid.

   WHY THESE DO NOT GO TO PendingHRBPReview
     Paid is terminal. Moving a paid award back into the review queue makes four
     things wrong at once:
       - the nominator's My Nominations page shows a disbursed award as awaiting
         review, which is simply false;
       - hrbp_router's approve/reject endpoints assume they are deciding whether
         money moves, and here it already has;
       - load_data() excludes PendingHRBPReview, so all 565 silently leave the
         training population the moment the UPDATE commits;
       - if the queue is never worked, there is no path back — nothing in the
         system moves a nomination from PendingHRBPReview to Paid.

     What we actually want from these rows is not a status change. It is a LABEL.
     This script gets the label without touching the money.

   WHY THE VERDICT DOES NOT GO THROUGH upsert_p2p_fraud_label
     backend.utils.sqlhelper2.upsert_p2p_fraud_label overwrites FraudScore with
     100/0 and RiskLevel with CRITICAL/NONE. So the instant a human labels a
     nomination, the model's prediction on it is gone. The rows that have ground
     truth and the rows that have a comparable model output become disjoint sets,
     and no precision/recall figure can ever be computed against human labels —
     which is exactly what the human-label evaluation requires.

     This script writes IsFraud + ConfirmedBy + ConfirmedAt and leaves FraudScore
     and RiskLevel alone. labels.py reads it that way: a non-null ConfirmedBy wins
     over the RiskLevel CASE, and the model's original score survives for scoring.

     upsert_p2p_fraud_label should be changed to match before the live HRBP path
     is exercised in anger. Until then, live reviews will still destroy scores.

   HOW TO USE
     PART A  — run the SELECT, export to Excel, have the reviewer fill Verdict
               with 1 (fraud) or 0 (legitimate), leaving blank for "cannot tell".
     PART B  — load the completed sheet into #Verdicts and run the MERGE.

   SAFETY
     - PART B is transactional; review the output then COMMIT or ROLLBACK.
     - Status, Amount and payment state are never written.
     - Idempotent: re-running with the same verdicts is a no-op beyond ConfirmedAt.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- @@ROWCOUNT is captured into a variable the statement after each DML, never
-- read later. PRINT is itself a statement and resets @@ROWCOUNT to 0, so
-- "PRINT 'header'; PRINT CAST(@@ROWCOUNT ...)" always reports 0 rows affected.
DECLARE @Rows INT;
DECLARE @TenantId    INT = 1;
DECLARE @SampleSize  INT = 60;    -- NULL / large number = the whole 565

/* ========================================================================== */
/* PART A — the worksheet                                                     */
/* ========================================================================== */
/* Stratified across score quartiles for the same reason as script 02: a
   reviewer shown only the top of the distribution cannot calibrate, and a
   reviewer shown 565 undifferentiated rows stops reading by row 80.

   Every row here is CRITICAL, so the model claims all 565 are fraud. If the
   reviewer comes back saying most are legitimate, that is the single most
   useful fact this project could learn — it means the CRITICAL threshold would
   auto-reject roughly 7% of all submissions with no human in the loop. */

WITH cohort AS (
    SELECT  n.NominationId,
            n.NominationDate,
            n.Amount,
            n.NominationDescription,
            p.FraudScore,
            p.FraudFlags,
            nom.FirstName + ' ' + nom.LastName AS NominatorName,
            ben.FirstName + ' ' + ben.LastName AS BeneficiaryName,
            NTILE(4) OVER (ORDER BY p.FraudScore DESC, n.NominationId) AS Quartile
    FROM    dbo.Nominations n
    JOIN    dbo.Users u            ON u.UserId       = n.NominatorId
    JOIN    dbo.Users nom          ON nom.UserId     = n.NominatorId
    JOIN    dbo.Users ben          ON ben.UserId     = n.BeneficiaryId
    JOIN    dbo.P2P_FraudScores p  ON p.NominationId = n.NominationId
    WHERE   u.TenantId  = @TenantId
      AND   n.Status    = 'Paid'
      AND   p.RiskLevel = 'CRITICAL'
      AND   p.ConfirmedBy IS NULL          -- not already adjudicated
),
ranked AS (
    SELECT c.*, ROW_NUMBER() OVER (PARTITION BY c.Quartile ORDER BY NEWID()) AS rn
    FROM   cohort c
)
SELECT  NominationId,
        Quartile,
        FraudScore,
        FraudFlags,
        NominationDate,
        Amount,
        NominatorName,
        BeneficiaryName,
        NominationDescription,
        CAST(NULL AS INT) AS Verdict,      -- reviewer fills: 1 fraud, 0 legit, blank unsure
        CAST(NULL AS NVARCHAR(400)) AS ReviewerNote
FROM    ranked
WHERE   rn <= CEILING(@SampleSize / 4.0)
ORDER BY Quartile, FraudScore DESC;


/* ========================================================================== */
/* PART B — apply the completed worksheet                                     */
/* ========================================================================== */
/*
DROP TABLE IF EXISTS #Verdicts;
CREATE TABLE #Verdicts (NominationId INT PRIMARY KEY, Verdict BIT NOT NULL);

-- Paste the reviewed rows here. Rows the reviewer left blank must be OMITTED,
-- not inserted as 0 — "cannot tell" is not "legitimate", and inserting it as 0
-- would recreate the exact unlabelled-means-clean defect labels.py exists to
-- expose.
INSERT INTO #Verdicts (NominationId, Verdict) VALUES
    (12345, 1),
    (12346, 0);

BEGIN TRANSACTION;

UPDATE  p
SET     p.IsFraud     = v.Verdict,
        p.ConfirmedBy = N'HRBP:retro-review',
        p.ConfirmedAt = SYSUTCDATETIME()
        -- FraudScore and RiskLevel deliberately NOT written: see header.
FROM    dbo.P2P_FraudScores p
JOIN    #Verdicts v ON v.NominationId = p.NominationId;

SET @Rows = @@ROWCOUNT;
PRINT '--- labels written: ' + CAST(@Rows AS VARCHAR(20)) + ' ---';

-- Model-versus-human agreement on the adjudicated set. This is the first
-- honest read on the CRITICAL threshold that has ever been available.
SELECT  COUNT(*)                                          AS Adjudicated,
        SUM(CASE WHEN v.Verdict = 1 THEN 1 ELSE 0 END)    AS Human_Fraud,
        SUM(CASE WHEN v.Verdict = 0 THEN 1 ELSE 0 END)    AS Human_Legitimate,
        CAST(100.0 * SUM(CASE WHEN v.Verdict = 1 THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0) AS DECIMAL(5,2))       AS Precision_At_CRITICAL_Pct
FROM    #Verdicts v
JOIN    dbo.P2P_FraudScores p ON p.NominationId = v.NominationId
WHERE   p.RiskLevel = 'CRITICAL';

-- Confirm nothing else moved.
SELECT  COUNT(*) AS Rows_With_NonPaid_Status
FROM    dbo.Nominations n
JOIN    #Verdicts v ON v.NominationId = n.NominationId
WHERE   n.Status <> 'Paid';

-- COMMIT TRANSACTION;
-- ROLLBACK TRANSACTION;

DROP TABLE IF EXISTS #Verdicts;
*/
