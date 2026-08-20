/* ============================================================================
   2026-08-20_04_rejection_lineage_diagnostic.sql
   ----------------------------------------------------------------------------
   READ-ONLY. No transaction, no writes, nothing to commit.

   Follow-up to script _01_. Two things its output raised:

     1. Script _01_'s UPDATE is global (correct - handler.py writes the same
        actor string for every tenant), but its Included_Before / Included_After
        verification is tenant-1 only. So the change touched every tenant and the
        proof covered one. Query A closes that gap.

     2. 525 of 545 Rejected nominations have RejectionActor = NULL. Migration
        0027 added the column with no backfill, so every nomination rejected
        before 0027 shipped has NULL forever. These rows are NOT excluded by
        load_data() - they are in the training population right now, carrying
        whatever label their P2P RiskLevel implies. Queries B-D characterise them.
   ============================================================================ */

SET NOCOUNT ON;

/* ── A. Per-tenant parity: did the rename move any tenant's label set? ─────── */
-- Old_Rule and New_Rule must be equal on EVERY row. Script _01_ proved this for
-- tenant 1 only.
SELECT  u.TenantId,
        COUNT(*)                                                    AS Total,
        SUM(CASE WHEN NOT (n.Status = 'Rejected'
                       AND n.RejectionActor = 'Fraud Detection')
                 THEN 1 ELSE 0 END)                                 AS Old_Rule,
        SUM(CASE WHEN NOT (n.Status = 'Rejected'
                       AND n.RejectionActor = 'Fraud Detection (Description)')
                 THEN 1 ELSE 0 END)                                 AS New_Rule
FROM    dbo.Nominations n
JOIN    dbo.Users u ON u.UserId = n.NominatorId
WHERE   n.Status NOT IN ('PendingHRBPReview')
GROUP BY u.TenantId
ORDER BY u.TenantId;
-- Old_Rule = New_Rule on every row  ->  the global UPDATE was label-neutral
-- everywhere, not just on tenant 1.


/* ── B. Where the rejections actually came from ────────────────────────────── */
-- Adds the tenant split the _01_ breakdown was missing, plus the audit trail.
-- updated_by tells you whether the app wrote the row or a bulk load did.
SELECT  u.TenantId,
        COALESCE(n.RejectionActor, '(NULL)')  AS RejectionActor,
        COALESCE(n.updated_by, '(NULL)')      AS updated_by,
        COUNT(*)                              AS Rows_,
        MIN(n.NominationDate)                 AS Earliest,
        MAX(n.NominationDate)                 AS Latest
FROM    dbo.Nominations n
JOIN    dbo.Users u ON u.UserId = n.NominatorId
WHERE   n.Status = 'Rejected'
GROUP BY u.TenantId, n.RejectionActor, n.updated_by
ORDER BY u.TenantId, Rows_ DESC;


/* ── C. What label are the 525 NULL-actor rejections carrying today? ───────── */
-- This is the part that matters for training. A rejected nomination with no P2P
-- score is currently labelled IsFraud = 0 - i.e. the models are being taught
-- that a rejected nomination is a clean one.
SELECT  COALESCE(p.RiskLevel, '(no P2P row)')       AS RiskLevel,
        COUNT(*)                                    AS Rows_,
        SUM(CASE WHEN p.RiskLevel IN ('HIGH','CRITICAL')
                 THEN 1 ELSE 0 END)                 AS Labelled_Fraud,
        SUM(CASE WHEN p.RiskLevel IS NULL OR p.RiskLevel NOT IN ('HIGH','CRITICAL')
                 THEN 1 ELSE 0 END)                 AS Labelled_Legitimate
FROM    dbo.Nominations n
JOIN    dbo.Users u ON u.UserId = n.NominatorId
LEFT JOIN dbo.P2P_FraudScores p ON p.NominationId = n.NominationId
WHERE   n.Status         = 'Rejected'
  AND   n.RejectionActor IS NULL
GROUP BY p.RiskLevel
ORDER BY Rows_ DESC;


/* ── D. Do they carry a reason, even without an actor? ─────────────────────── */
-- RejectionReason survives independently. If these rows have reason text, the
-- actor may be recoverable by pattern - Check A reasons come from
-- description_check and read very differently from a manager's free text or an
-- LLM-generated fraud explanation. If the reasons are also NULL, the decision
-- is unrecoverable and these 525 are permanently un-attributable.
SELECT  CASE WHEN n.RejectionReason IS NULL THEN 'no reason'
             ELSE 'has reason' END                  AS ReasonPresent,
        COUNT(*)                                    AS Rows_
FROM    dbo.Nominations n
WHERE   n.Status         = 'Rejected'
  AND   n.RejectionActor IS NULL
GROUP BY CASE WHEN n.RejectionReason IS NULL THEN 'no reason' ELSE 'has reason' END;

-- Sample of any that do, to see whether the actor is inferable from the text.
SELECT TOP 20
        n.NominationId,
        n.NominationDate,
        n.updated_by,
        LEFT(n.RejectionReason, 120) AS Reason_Excerpt
FROM    dbo.Nominations n
WHERE   n.Status          = 'Rejected'
  AND   n.RejectionActor  IS NULL
  AND   n.RejectionReason IS NOT NULL
ORDER BY n.NominationDate DESC;
