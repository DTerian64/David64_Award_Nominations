/* ============================================================================
   2026-08-21_05_inspect_nomination.sql
   ----------------------------------------------------------------------------
   READ-ONLY. Reconstructs, by hand, what the GNN could have been looking at
   when it scored one nomination.

   WHY THIS SCRIPT EXISTS
     The GNN stores a score and nothing else. Its decoder is an MLP over
     3 x 64 learned embedding dimensions plus 7 nomination features, and those
     192 embedding dimensions have no individual meaning — they are coordinates
     the encoder invented during training. There is no SHAP, no attribution, no
     stored flag. Asking "why 100?" has no answer inside the system.

     So this script does the manual version: it shows the inputs the model was
     given, and lets a human form a hypothesis. That is not an explanation — it
     is the absence of one, made legible. Automating this reconciliation is
     precisely what ELCE proposes to invent.

   SCOPE NOTE
     Every aggregate below is computed over the GNN's 180-day training window
     (GNN_WINDOW_DAYS), not over all history. Showing lifetime figures would be
     showing features the model never saw.
   ============================================================================ */

SET NOCOUNT ON;

DECLARE @NominationId INT = 3050;      -- <<< the nomination to inspect
DECLARE @TenantId     INT = 1;
DECLARE @WindowDays   INT = 180;       -- must match GNN_WINDOW_DAYS for the run
DECLARE @WindowStart  DATE = CAST(DATEADD(DAY, -@WindowDays, GETDATE()) AS DATE);

DECLARE @NominatorId INT, @BeneficiaryId INT, @ApproverId INT;
SELECT  @NominatorId = NominatorId, @BeneficiaryId = BeneficiaryId, @ApproverId = ApproverId
FROM    dbo.Nominations WHERE NominationId = @NominationId;

/* ── A. The nomination, and the 7 features the decoder actually receives ───── */
-- These are the ONLY non-embedding inputs to the score. If none of them looks
-- unusual, then whatever drove the score came through the embeddings, i.e. from
-- the graph neighbourhood rather than from this nomination.
SELECT
    n.NominationId, n.Status, n.NominationDate, n.Amount,
    -- AmountZScore, as the model computes it: standardised within the window
    CAST((n.Amount - w.AvgAmt) / NULLIF(w.SdAmt, 0) AS DECIMAL(8,3)) AS AmountZScore,
    DATEPART(WEEKDAY, n.NominationDate)                              AS DayOfWeek,
    DATEPART(MONTH,   n.NominationDate)                              AS [Month],
    CASE WHEN DATEPART(WEEKDAY, n.NominationDate) IN (1,7) THEN 1 ELSE 0 END AS IsWeekend,
    CASE WHEN n.Amount > w.AvgAmt + 2*w.SdAmt THEN 1 ELSE 0 END      AS IsHighAmount,
    CASE WHEN n.ApproverId IS NULL THEN 0 ELSE 1 END                 AS HasApprover,
    LEN(COALESCE(n.NominationDescription, ''))                       AS DescriptionLength
FROM   dbo.Nominations n
CROSS  JOIN (
    SELECT AVG(CAST(n2.Amount AS FLOAT)) AS AvgAmt, STDEV(CAST(n2.Amount AS FLOAT)) AS SdAmt
    FROM   dbo.Nominations n2
    JOIN   dbo.Users u2 ON u2.UserId = n2.NominatorId
    WHERE  u2.TenantId = @TenantId AND n2.NominationDate >= @WindowStart
) w
WHERE  n.NominationId = @NominationId;


/* ── B. Per-user features for the three actors ─────────────────────────────── */
-- These feed the encoder. The embeddings are built from these PLUS the graph
-- structure around each user, so a plain row here can still yield an extreme
-- embedding — that is exactly the part no query can show you.
WITH win AS (
    SELECT n.NominationId, n.NominatorId, n.BeneficiaryId, n.ApproverId, n.Amount
    FROM   dbo.Nominations n
    JOIN   dbo.Users u ON u.UserId = n.NominatorId
    WHERE  u.TenantId = @TenantId AND n.NominationDate >= @WindowStart
),
actors AS (
    SELECT @NominatorId AS UserId, 'nominator' AS Role
    UNION ALL SELECT @BeneficiaryId, 'beneficiary'
    UNION ALL SELECT @ApproverId,    'approver'
)
SELECT
    a.Role,
    a.UserId,
    usr.FirstName + ' ' + usr.LastName                                     AS Name,
    (SELECT COUNT(*) FROM win w WHERE w.NominatorId   = a.UserId)          AS NominationsMade,
    (SELECT COUNT(*) FROM win w WHERE w.BeneficiaryId = a.UserId)          AS NominationsReceived,
    (SELECT COUNT(*) FROM win w WHERE w.ApproverId    = a.UserId)          AS NominationsApproved,
    (SELECT CAST(AVG(CAST(w.Amount AS FLOAT)) AS DECIMAL(10,2))
       FROM win w WHERE w.NominatorId   = a.UserId)                        AS AvgAmountGiven,
    (SELECT CAST(STDEV(CAST(w.Amount AS FLOAT)) AS DECIMAL(10,2))
       FROM win w WHERE w.NominatorId   = a.UserId)                        AS StdAmountGiven,
    (SELECT CAST(AVG(CAST(w.Amount AS FLOAT)) AS DECIMAL(10,2))
       FROM win w WHERE w.BeneficiaryId = a.UserId)                        AS AvgAmountReceived,
    (SELECT COUNT(DISTINCT w.BeneficiaryId)
       FROM win w WHERE w.NominatorId   = a.UserId)                        AS UniqueBeneficiaries,
    (SELECT COUNT(DISTINCT w.NominatorId)
       FROM win w WHERE w.BeneficiaryId = a.UserId)                        AS UniqueNominators,
    -- ConcentrationRatio: most nominations sent to any one person / total sent.
    -- 1.0 means every nomination this user made went to the same beneficiary.
    (SELECT CAST(MAX(c.Cnt) * 1.0 / NULLIF(SUM(c.Cnt), 0) AS DECIMAL(5,3))
       FROM (SELECT COUNT(*) AS Cnt FROM win w
             WHERE w.NominatorId = a.UserId GROUP BY w.BeneficiaryId) c)   AS ConcentrationRatio,
    -- ReciprocalPairCount: counterparties this user nominated who also
    -- nominated them back. The classic collusion signature.
    (SELECT COUNT(DISTINCT w1.BeneficiaryId)
       FROM win w1
       WHERE w1.NominatorId = a.UserId
         AND EXISTS (SELECT 1 FROM win w2
                     WHERE w2.NominatorId   = w1.BeneficiaryId
                       AND w2.BeneficiaryId = a.UserId))                   AS ReciprocalPairCount
FROM   actors a
JOIN   dbo.Users usr ON usr.UserId = a.UserId;


/* ── C. Both models' scores, side by side ─────────────────────────────────── */
SELECT
    'RandomForest' AS Model, p.FraudScore, p.RiskLevel, p.FraudFlags,
    p.ConfirmedBy, p.ConfirmedAt, NULL AS ModelVersion, NULL AS ScoringMode
FROM   dbo.P2P_FraudScores p WHERE p.NominationId = @NominationId
UNION ALL
SELECT
    'GNN', g.FraudScore, g.RiskLevel, g.FraudFlags,
    NULL, NULL, g.ModelVersion, g.ScoringMode
FROM   dbo.GNN_FraudScores g WHERE g.NominationId = @NominationId;
-- GNN.FraudFlags is expected NULL: the column exists in migration 0040 but
-- train_gnn_model never writes it. That empty column is the shape of the gap.


/* ── D. Rule-layer findings touching this nomination or its actors ─────────── */
-- The graph detectors DO record their evidence — AffectedUsers and
-- NominationIds. If a finding names these people, that is the closest thing to
-- a justification available anywhere in the system, and it came from the rules,
-- not from the GNN.
SELECT  f.FindingId, f.PatternType, f.Severity, f.DetectedAt,
        f.AffectedUsers, f.NominationIds, f.Detail
FROM    dbo.GraphPatternFindings f
WHERE   f.TenantId = @TenantId
  AND  (f.NominationIds LIKE '%' + CAST(@NominationId AS VARCHAR(20)) + '%'
    OR  f.AffectedUsers LIKE '%' + CAST(@NominatorId   AS VARCHAR(20)) + '%'
    OR  f.AffectedUsers LIKE '%' + CAST(@BeneficiaryId AS VARCHAR(20)) + '%')
ORDER BY f.DetectedAt DESC;


/* ── E. The direct neighbourhood — every edge touching either party ────────── */
-- Message passing propagates along these edges, so this is the raw material the
-- embeddings were built from. Look for cycles, reciprocity, and repetition.
SELECT  n.NominationId, n.NominationDate, n.Amount, n.Status,
        nom.FirstName + ' ' + nom.LastName AS Nominator,
        ben.FirstName + ' ' + ben.LastName AS Beneficiary,
        apr.FirstName + ' ' + apr.LastName AS Approver,
        CASE WHEN n.NominationId = @NominationId THEN '<<< THIS ONE' ELSE '' END AS Marker
FROM    dbo.Nominations n
JOIN    dbo.Users nom ON nom.UserId = n.NominatorId
JOIN    dbo.Users ben ON ben.UserId = n.BeneficiaryId
LEFT JOIN dbo.Users apr ON apr.UserId = n.ApproverId
WHERE   n.NominationDate >= @WindowStart
  AND  (n.NominatorId   IN (@NominatorId, @BeneficiaryId)
    OR  n.BeneficiaryId IN (@NominatorId, @BeneficiaryId))
ORDER BY n.NominationDate;
