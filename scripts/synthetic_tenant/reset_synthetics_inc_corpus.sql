/*
Purpose
=======
One-time, tenant-scoped removal of the deployed Synthetics Inc. v1.1 corpus so
the v2.0 causal-scenario corpus can be loaded with the existing Python seeder.

This script preserves dbo.Tenants, dbo.Users, roles, categories, email
templates, Graph/GNN scoring policies, Entra identities, DNS, and application
configuration. It removes only the manifest-owned nomination corpus and data
derived from that corpus.

Safety and usage
================
1. Stop or confirm there is no running fraud-analytics-job for this database.
2. Run this entire file with credentials that can delete the listed rows.
3. The default @CommitChanges = 0 performs a fully transactional preview and
   rolls it back.
4. Review the inventory result sets and all preflight checks.
5. Change @CommitChanges to 1 and run the entire file again to commit.
6. Run the existing v2.0 seeder --apply command documented in README.md.

Do not change the organization ID, tenant identity, expected v1.1 corpus hash,
generation run ID, or expected row counts merely to bypass a failed preflight.
Investigate the difference instead.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @CommitChanges BIT = 0;  -- Change to 1 only after reviewing a preview.
DECLARE @OrganizationId VARCHAR(36) =
    'f74bff31-f42f-4461-a1dd-e1ae978c1abe';
DECLARE @ExpectedTenantName NVARCHAR(256) = N'Synthetics Inc';
DECLARE @ExpectedDomain NVARCHAR(256) = N'synthetic-awards.terianix.ai';
DECLARE @ExpectedCorpusSha256 VARCHAR(64) =
    '99d7dec19ee837eddcb8cf4785e98d054183df1c7747e5d872e16d43a07f7f5b';
DECLARE @ExpectedGenerationRunId VARCHAR(36) =
    '1b182f36-1d25-5e01-89fd-059aadf4eddc';
DECLARE @ExpectedUserCount INT = 401;
DECLARE @ExpectedNominationCount INT = 5000;
DECLARE @TenantId INT;
DECLARE @ErrorMessage NVARCHAR(2048);
DECLARE @LockResult INT;

BEGIN TRANSACTION;

EXEC @LockResult = sys.sp_getapplock
    @Resource = N'synthetics-inc-corpus-reset',
    @LockMode = 'Exclusive',
    @LockOwner = 'Transaction',
    @LockTimeout = 0;

IF @LockResult < 0
    THROW 51000, 'Could not acquire the Synthetics Inc. corpus reset lock.', 1;

IF (
    SELECT COUNT(*)
    FROM dbo.Tenants WITH (UPDLOCK, HOLDLOCK)
    WHERE AzureAdTenantId = @OrganizationId
) <> 1
    THROW 51000, 'Organization ID must resolve to exactly one SQL tenant.', 1;

SELECT @TenantId = TenantId
FROM dbo.Tenants WITH (UPDLOCK, HOLDLOCK)
WHERE AzureAdTenantId = @OrganizationId;

IF NOT EXISTS (
    SELECT 1
    FROM dbo.Tenants
    WHERE TenantId = @TenantId
      AND TenantName = @ExpectedTenantName
      AND Domain = @ExpectedDomain
      AND is_synthetic = 1
)
    THROW 51000, 'Tenant identity or is_synthetic preflight failed.', 1;

IF (
    SELECT COUNT(*) FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
    WHERE TenantId = @TenantId
) <> @ExpectedUserCount
    THROW 51000, 'Expected exactly 401 Synthetics Inc. SQL users.', 1;

CREATE TABLE #OwnedNominationIds (
    NominationId INT NOT NULL PRIMARY KEY
);

INSERT INTO #OwnedNominationIds (NominationId)
SELECT decision_result.NominationId
FROM dbo.IntegrityDecisionResults AS decision_result WITH (UPDLOCK, HOLDLOCK)
WHERE decision_result.TenantId = @TenantId
  AND decision_result.SourceMessageId LIKE N'synthetic:%'
  AND decision_result.TrainingDispositionSource = 'SYNTHETIC_GROUND_TRUTH'
  AND ISJSON(decision_result.TrainingDispositionMetadataJson) = 1
  AND JSON_VALUE(
        decision_result.TrainingDispositionMetadataJson,
        '$.corpus_sha256'
      ) = @ExpectedCorpusSha256
  AND JSON_VALUE(
        decision_result.TrainingDispositionMetadataJson,
        '$.generation_run_id'
      ) = @ExpectedGenerationRunId;

IF (SELECT COUNT(*) FROM #OwnedNominationIds) <> @ExpectedNominationCount
    THROW 51000, 'Expected exactly 5,000 manifest-owned v1.1 decisions.', 1;

IF (
    SELECT COUNT(*)
    FROM dbo.IntegrityDecisionResults
    WHERE TenantId = @TenantId
) <> @ExpectedNominationCount
    THROW 51000, 'Tenant contains decisions outside the expected v1.1 corpus.', 1;

IF (
    SELECT COUNT(*)
    FROM dbo.Nominations AS nomination WITH (UPDLOCK, HOLDLOCK)
    INNER JOIN dbo.Users AS nominator
        ON nominator.UserId = nomination.NominatorId
    WHERE nominator.TenantId = @TenantId
) <> @ExpectedNominationCount
    THROW 51000, 'Tenant nomination count is not exactly 5,000.', 1;

IF EXISTS (
    SELECT 1
    FROM dbo.Nominations AS nomination
    INNER JOIN dbo.Users AS nominator
        ON nominator.UserId = nomination.NominatorId
    LEFT JOIN #OwnedNominationIds AS owned
        ON owned.NominationId = nomination.NominationId
    WHERE nominator.TenantId = @TenantId
      AND owned.NominationId IS NULL
)
    THROW 51000, 'Tenant contains a nomination not owned by the v1.1 manifest.', 1;

IF EXISTS (
    SELECT 1
    FROM dbo.Nominations AS nomination
    INNER JOIN dbo.Users AS nominator
        ON nominator.UserId = nomination.NominatorId
    INNER JOIN dbo.Users AS beneficiary
        ON beneficiary.UserId = nomination.BeneficiaryId
    INNER JOIN dbo.Users AS approver
        ON approver.UserId = nomination.ApproverId
    WHERE nomination.NominationId IN (
        SELECT NominationId FROM #OwnedNominationIds
    )
      AND (
          nominator.TenantId <> @TenantId
          OR beneficiary.TenantId <> @TenantId
          OR approver.TenantId <> @TenantId
      )
)
    THROW 51000, 'The owned corpus contains a cross-tenant user reference.', 1;

/*
Fail closed if a later migration introduces a new direct nomination child.
The reset must then be reviewed and extended instead of bypassing its FK.
*/
IF EXISTS (
    SELECT 1
    FROM sys.foreign_key_columns AS foreign_key_column
    INNER JOIN sys.tables AS child_table
        ON child_table.object_id = foreign_key_column.parent_object_id
    INNER JOIN sys.schemas AS child_schema
        ON child_schema.schema_id = child_table.schema_id
    WHERE foreign_key_column.referenced_object_id =
          OBJECT_ID(N'dbo.Nominations')
      AND NOT (
          child_schema.name = N'dbo'
          AND child_table.name IN (
              N'IntegrityDecisionResults', N'payroll_submissions'
          )
      )
)
BEGIN
    SELECT
        child_schema.name AS UnexpectedSchema,
        child_table.name AS UnexpectedNominationChild
    FROM sys.foreign_key_columns AS foreign_key_column
    INNER JOIN sys.tables AS child_table
        ON child_table.object_id = foreign_key_column.parent_object_id
    INNER JOIN sys.schemas AS child_schema
        ON child_schema.schema_id = child_table.schema_id
    WHERE foreign_key_column.referenced_object_id =
          OBJECT_ID(N'dbo.Nominations')
      AND NOT (
          child_schema.name = N'dbo'
          AND child_table.name IN (
              N'IntegrityDecisionResults', N'payroll_submissions'
          )
      );
    THROW 51000, 'An unexpected nomination FK child requires reset review.', 1;
END;

/* Before inventory. Optional tables return zero when absent. */
SELECT N'dbo.Nominations' AS TableName, COUNT_BIG(*) AS RowsToRemove
FROM dbo.Nominations AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId
UNION ALL
SELECT N'dbo.IntegrityDecisionResults', COUNT_BIG(*)
FROM dbo.IntegrityDecisionResults AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId
UNION ALL
SELECT N'dbo.Nomination_Logs', COUNT_BIG(*)
FROM dbo.Nomination_Logs AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.nomination_id
UNION ALL
SELECT N'dbo.ProcessedEvents', COUNT_BIG(*)
FROM dbo.ProcessedEvents AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId
UNION ALL
SELECT N'dbo.NomGraph_Nominated', COUNT_BIG(*)
FROM dbo.NomGraph_Nominated AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId
UNION ALL
SELECT N'dbo.NomGraph_NominationEmbedding', COUNT_BIG(*)
FROM dbo.NomGraph_NominationEmbedding AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId
UNION ALL
SELECT N'dbo.GraphPatternFindings', COUNT_BIG(*)
FROM dbo.GraphPatternFindings WHERE TenantId = @TenantId
UNION ALL
SELECT N'dbo.UserGraphFlags', COUNT_BIG(*)
FROM dbo.UserGraphFlags WHERE TenantId = @TenantId
UNION ALL
SELECT N'dbo.ApproverPairFlags', COUNT_BIG(*)
FROM dbo.ApproverPairFlags WHERE TenantId = @TenantId
UNION ALL
SELECT N'dbo.GNN_UserEmbeddings', COUNT_BIG(*)
FROM dbo.GNN_UserEmbeddings WHERE TenantId = @TenantId
UNION ALL
SELECT N'dbo.GraphScoringChangeRequests', COUNT_BIG(*)
FROM dbo.GraphScoringChangeRequests WHERE TenantId = @TenantId
ORDER BY TableName;

/* FK and non-FK nomination children, then tenant-level derived data. */
IF OBJECT_ID(N'dbo.payroll_submissions', N'U') IS NOT NULL
    DELETE submission
    FROM dbo.payroll_submissions AS submission
    INNER JOIN #OwnedNominationIds AS owned
        ON owned.NominationId = submission.nomination_id;

DELETE item
FROM dbo.Nomination_Logs AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.nomination_id;

DELETE item
FROM dbo.ProcessedEvents AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId;

DELETE item
FROM dbo.NomGraph_NominationEmbedding AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId;

DELETE item
FROM dbo.NomGraph_Nominated AS item
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = item.NominationId;

DELETE FROM dbo.GraphScoringChangeRequests WHERE TenantId = @TenantId;
DELETE FROM dbo.GraphPatternFindings WHERE TenantId = @TenantId;
DELETE FROM dbo.UserGraphFlags WHERE TenantId = @TenantId;
DELETE FROM dbo.ApproverPairFlags WHERE TenantId = @TenantId;
DELETE FROM dbo.GNN_UserEmbeddings WHERE TenantId = @TenantId;

DELETE decision_result
FROM dbo.IntegrityDecisionResults AS decision_result
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = decision_result.NominationId;

DELETE nomination
FROM dbo.Nominations AS nomination
INNER JOIN #OwnedNominationIds AS owned
    ON owned.NominationId = nomination.NominationId;

/*
Invalidate all old serving pointers. Blob artifacts remain immutable, but no
component can serve a model or snapshot trained from the removed corpus.
Temporal history remains available as an audit trail.
*/
UPDATE dbo.IntegrityComponentStatus
SET ServingStatus = 'UNAVAILABLE',
    ServingVersion = NULL,
    ServingAsOf = NULL,
    LastAttemptStatus = 'SKIPPED',
    ReasonCode = 'SYNTHETIC_CORPUS_RESET',
    ReasonDetail = N'Awaiting analytics rebuild from synthetics-inc-v2.0',
    DiagnosticsJson = N'{"reset_reason":"synthetics-inc-v2.0 causal corpus replacement"}',
    LastAttemptAt = SYSUTCDATETIME(),
    LastSuccessfulAt = NULL,
    RunId = NULL,
    UpdatedAt = SYSUTCDATETIME(),
    UpdatedBy = N'svc:synthetic-corpus-reset:v1'
WHERE TenantId = @TenantId;

IF EXISTS (
    SELECT 1
    FROM dbo.Nominations AS nomination
    INNER JOIN dbo.Users AS nominator
        ON nominator.UserId = nomination.NominatorId
    WHERE nominator.TenantId = @TenantId
)
    THROW 51000, 'Post-delete check found remaining tenant nominations.', 1;

IF EXISTS (
    SELECT 1 FROM dbo.IntegrityDecisionResults WHERE TenantId = @TenantId
)
    THROW 51000, 'Post-delete check found remaining tenant decisions.', 1;

IF (SELECT COUNT(*) FROM dbo.Users WHERE TenantId = @TenantId) <>
   @ExpectedUserCount
    THROW 51000, 'User preservation check failed.', 1;

SELECT
    @TenantId AS TenantId,
    @ExpectedTenantName AS TenantName,
    (SELECT COUNT(*) FROM dbo.Users WHERE TenantId = @TenantId)
        AS PreservedUsers,
    (SELECT COUNT(*)
     FROM dbo.Nominations AS nomination
     INNER JOIN dbo.Users AS nominator
        ON nominator.UserId = nomination.NominatorId
     WHERE nominator.TenantId = @TenantId) AS RemainingNominations,
    (SELECT COUNT(*)
     FROM dbo.IntegrityDecisionResults
     WHERE TenantId = @TenantId) AS RemainingDecisions,
    CASE WHEN @CommitChanges = 1 THEN N'COMMIT' ELSE N'ROLLBACK PREVIEW' END
        AS RequestedOutcome;

IF @CommitChanges = 1
BEGIN
    COMMIT TRANSACTION;
    PRINT 'Committed: Synthetics Inc. v1.1 corpus and derived data removed.';
END
ELSE
BEGIN
    ROLLBACK TRANSACTION;
    PRINT 'Preview only: all changes rolled back. Set @CommitChanges = 1 to apply.';
END;

