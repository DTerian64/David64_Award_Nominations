-- ============================================================================
-- FRAUD DETECTION STORED PROCEDURE
-- Analyzes nomination patterns to identify potential fraud
-- ============================================================================

-- First, create a table to store fraud scores and flags
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'FraudScores')
BEGIN
    CREATE TABLE [dbo].[FraudScores](
        [FraudScoreId] [int] IDENTITY(1,1) NOT NULL,
        [NominationId] [int] NOT NULL,
        [FraudScore] [decimal](5,2) NOT NULL,
        [FraudFlags] [nvarchar](500) NULL,
        [RiskLevel] [nvarchar](20) NOT NULL,
        [AnalysisDate] [datetime2](7) NOT NULL DEFAULT GETDATE(),
        PRIMARY KEY CLUSTERED ([FraudScoreId] ASC),
        CONSTRAINT FK_FraudScores_Nominations FOREIGN KEY([NominationId])
            REFERENCES [dbo].[Nominations] ([NominationId])
    )
END
GO

-- Create analytics table to store fraud patterns
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'FraudAnalytics')
BEGIN
    CREATE TABLE [dbo].[FraudAnalytics](
        [AnalyticsId] [int] IDENTITY(1,1) NOT NULL,
        [UserId] [int] NOT NULL,
        [AnalysisType] [nvarchar](50) NOT NULL,
        [MetricValue] [decimal](10,2) NOT NULL,
        [Description] [nvarchar](500) NULL,
        [AnalysisDate] [datetime2](7) NOT NULL DEFAULT GETDATE(),
        PRIMARY KEY CLUSTERED ([AnalyticsId] ASC)
    )
END
GO

-- Main Fraud Detection Procedure
CREATE OR ALTER PROCEDURE [dbo].[sp_DetectFraud]
    @ThresholdScore DECIMAL(5,2) = 50.0,  -- Minimum score to flag as suspicious
    @DaysToAnalyze INT = 365               -- Number of days to look back
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @AnalysisStartDate DATE = DATEADD(DAY, -@DaysToAnalyze, GETDATE())
    
    -- Clear previous fraud scores for re-analysis
    TRUNCATE TABLE [dbo].[FraudScores]
    TRUNCATE TABLE [dbo].[FraudAnalytics]
    
    -- ========================================================================
    -- FRAUD PATTERN 1: High Frequency Nominations
    -- Detect users nominating unusually frequently
    -- ========================================================================
    INSERT INTO [dbo].[FraudAnalytics] (UserId, AnalysisType, MetricValue, Description)
    SELECT 
        NominatorId,
        'HighFrequency',
        COUNT(*) as NominationCount,
        'User has nominated ' + CAST(COUNT(*) AS NVARCHAR) + ' times in ' + CAST(@DaysToAnalyze AS NVARCHAR) + ' days'
    FROM [dbo].[Nominations]
    WHERE NominationDate >= @AnalysisStartDate
    GROUP BY NominatorId
    HAVING COUNT(*) > 50  -- More than 50 nominations
    
    -- ========================================================================
    -- FRAUD PATTERN 2: Same Beneficiary Repeatedly
    -- Detect nominators who repeatedly nominate the same person
    -- ========================================================================
    INSERT INTO [dbo].[FraudAnalytics] (UserId, AnalysisType, MetricValue, Description)
    SELECT 
        NominatorId,
        'RepeatedBeneficiary',
        COUNT(*) as RepeatCount,
        'Nominated BeneficiaryId=' + CAST(BeneficiaryId AS NVARCHAR) + ' ' + CAST(COUNT(*) AS NVARCHAR) + ' times'
    FROM [dbo].[Nominations]
    WHERE NominationDate >= @AnalysisStartDate
    GROUP BY NominatorId, BeneficiaryId
    HAVING COUNT(*) > 5  -- Same person more than 5 times
    
    -- ========================================================================
    -- FRAUD PATTERN 3: Circular Nominations
    -- Detect reciprocal nomination patterns (A nominates B, B nominates A)
    -- ========================================================================
    INSERT INTO [dbo].[FraudAnalytics] (UserId, AnalysisType, MetricValue, Description)
    SELECT DISTINCT
        n1.NominatorId,
        'CircularNomination',
        COUNT(*) as CircularCount,
        'Reciprocal nominations with UserId=' + CAST(n1.BeneficiaryId AS NVARCHAR)
    FROM [dbo].[Nominations] n1
    INNER JOIN [dbo].[Nominations] n2 
        ON n1.NominatorId = n2.BeneficiaryId 
        AND n1.BeneficiaryId = n2.NominatorId
    WHERE n1.NominationDate >= @AnalysisStartDate
    GROUP BY n1.NominatorId, n1.BeneficiaryId
    HAVING COUNT(*) >= 2
    
    -- ========================================================================
    -- FRAUD PATTERN 4: Unusually High Dollar Amounts
    -- Detect nominations with amounts significantly above average
    -- ========================================================================
    DECLARE @AvgAmount DECIMAL(10,2)
    DECLARE @StdDevAmount DECIMAL(10,2)
    
    SELECT 
        @AvgAmount = AVG(CAST(DollarAmount AS DECIMAL(10,2))),
        @StdDevAmount = STDEV(CAST(DollarAmount AS DECIMAL(10,2)))
    FROM [dbo].[Nominations]
    WHERE NominationDate >= @AnalysisStartDate
    
    INSERT INTO [dbo].[FraudAnalytics] (UserId, AnalysisType, MetricValue, Description)
    SELECT 
        NominatorId,
        'HighDollarAmount',
        AVG(CAST(DollarAmount AS DECIMAL(10,2))) as AvgAmount,
        'Average nomination amount: $' + CAST(AVG(DollarAmount) AS NVARCHAR) + 
        ' (Overall avg: $' + CAST(@AvgAmount AS NVARCHAR) + ')'
    FROM [dbo].[Nominations]
    WHERE NominationDate >= @AnalysisStartDate
        AND DollarAmount > (@AvgAmount + 2 * @StdDevAmount)  -- 2 standard deviations above mean
    GROUP BY NominatorId
    HAVING COUNT(*) >= 3
    
    -- ========================================================================
    -- FRAUD PATTERN 5: Rapid Approval Times
    -- Detect nominations approved suspiciously quickly
    -- ========================================================================
    INSERT INTO [dbo].[FraudAnalytics] (UserId, AnalysisType, MetricValue, Description)
    SELECT 
        ApproverId,
        'RapidApproval',
        AVG(CAST(DATEDIFF(HOUR, NominationDate, ApprovedDate) AS DECIMAL(10,2))) as AvgHours,
        'Average approval time: ' + CAST(AVG(DATEDIFF(HOUR, NominationDate, ApprovedDate)) AS NVARCHAR) + ' hours'
    FROM [dbo].[Nominations]
    WHERE NominationDate >= @AnalysisStartDate
        AND ApprovedDate IS NOT NULL
        AND DATEDIFF(HOUR, NominationDate, ApprovedDate) < 1  -- Approved in less than 1 hour
    GROUP BY ApproverId
    HAVING COUNT(*) >= 5
    
    -- ========================================================================
    -- FRAUD PATTERN 6: Self-Dealing Networks
    -- Detect tight groups of users nominating only each other
    -- ========================================================================
    INSERT INTO [dbo].[FraudAnalytics] (UserId, AnalysisType, MetricValue, Description)
    SELECT 
        NominatorId,
        'SelfDealingNetwork',
        COUNT(DISTINCT BeneficiaryId) as UniqueRecipients,
        'Only nominates ' + CAST(COUNT(DISTINCT BeneficiaryId) AS NVARCHAR) + 
        ' unique people out of ' + CAST(COUNT(*) AS NVARCHAR) + ' nominations'
    FROM [dbo].[Nominations]
    WHERE NominationDate >= @AnalysisStartDate
    GROUP BY NominatorId
    HAVING COUNT(*) >= 10 
        AND COUNT(DISTINCT BeneficiaryId) <= 3  -- Many nominations to very few people
    
    -- ========================================================================
    -- CALCULATE FRAUD SCORES FOR EACH NOMINATION
    -- ========================================================================
    ;WITH FraudMetrics AS (
        SELECT 
            n.NominationId,
            n.NominatorId,
            n.BeneficiaryId,
            n.ApproverId,
            n.DollarAmount,
            n.NominationDate,
            n.ApprovedDate,
            
            -- Pattern scoring
            CASE WHEN EXISTS (
                SELECT 1 FROM FraudAnalytics fa 
                WHERE fa.UserId = n.NominatorId 
                AND fa.AnalysisType = 'HighFrequency'
            ) THEN 15 ELSE 0 END AS HighFrequencyScore,
            
            CASE WHEN EXISTS (
                SELECT 1 FROM FraudAnalytics fa 
                WHERE fa.UserId = n.NominatorId 
                AND fa.AnalysisType = 'RepeatedBeneficiary'
                AND fa.Description LIKE '%BeneficiaryId=' + CAST(n.BeneficiaryId AS NVARCHAR) + '%'
            ) THEN 20 ELSE 0 END AS RepeatedBeneficiaryScore,
            
            CASE WHEN EXISTS (
                SELECT 1 FROM FraudAnalytics fa 
                WHERE fa.UserId = n.NominatorId 
                AND fa.AnalysisType = 'CircularNomination'
            ) THEN 25 ELSE 0 END AS CircularScore,
            
            CASE WHEN n.DollarAmount > (@AvgAmount + 2 * @StdDevAmount) 
                THEN 20 ELSE 0 END AS HighAmountScore,
            
            CASE WHEN DATEDIFF(HOUR, n.NominationDate, n.ApprovedDate) < 1 
                THEN 10 ELSE 0 END AS RapidApprovalScore,
            
            CASE WHEN EXISTS (
                SELECT 1 FROM FraudAnalytics fa 
                WHERE fa.UserId = n.NominatorId 
                AND fa.AnalysisType = 'SelfDealingNetwork'
            ) THEN 10 ELSE 0 END AS SelfDealingScore
            
        FROM [dbo].[Nominations] n
        WHERE n.NominationDate >= @AnalysisStartDate
    )
    INSERT INTO [dbo].[FraudScores] (NominationId, FraudScore, FraudFlags, RiskLevel)
    SELECT 
        NominationId,
        (HighFrequencyScore + RepeatedBeneficiaryScore + CircularScore + 
         HighAmountScore + RapidApprovalScore + SelfDealingScore) AS FraudScore,
        CONCAT_WS(', ',
            CASE WHEN HighFrequencyScore > 0 THEN 'High Frequency' END,
            CASE WHEN RepeatedBeneficiaryScore > 0 THEN 'Repeated Beneficiary' END,
            CASE WHEN CircularScore > 0 THEN 'Circular Nomination' END,
            CASE WHEN HighAmountScore > 0 THEN 'High Amount' END,
            CASE WHEN RapidApprovalScore > 0 THEN 'Rapid Approval' END,
            CASE WHEN SelfDealingScore > 0 THEN 'Self-Dealing Network' END
        ) AS FraudFlags,
        CASE 
            WHEN (HighFrequencyScore + RepeatedBeneficiaryScore + CircularScore + 
                  HighAmountScore + RapidApprovalScore + SelfDealingScore) >= 70 THEN 'CRITICAL'
            WHEN (HighFrequencyScore + RepeatedBeneficiaryScore + CircularScore + 
                  HighAmountScore + RapidApprovalScore + SelfDealingScore) >= 50 THEN 'HIGH'
            WHEN (HighFrequencyScore + RepeatedBeneficiaryScore + CircularScore + 
                  HighAmountScore + RapidApprovalScore + SelfDealingScore) >= 30 THEN 'MEDIUM'
            WHEN (HighFrequencyScore + RepeatedBeneficiaryScore + CircularScore + 
                  HighAmountScore + RapidApprovalScore + SelfDealingScore) > 0 THEN 'LOW'
            ELSE 'NONE'
        END AS RiskLevel
    FROM FraudMetrics
    WHERE (HighFrequencyScore + RepeatedBeneficiaryScore + CircularScore + 
           HighAmountScore + RapidApprovalScore + SelfDealingScore) >= @ThresholdScore
           OR @ThresholdScore = 0  -- If threshold is 0, score all nominations
    
    -- ========================================================================
    -- RETURN SUMMARY REPORT
    -- ========================================================================
    SELECT 
        'Fraud Analysis Summary' AS ReportSection,
        COUNT(*) AS TotalNominationsAnalyzed,
        SUM(CASE WHEN RiskLevel = 'CRITICAL' THEN 1 ELSE 0 END) AS CriticalRisk,
        SUM(CASE WHEN RiskLevel = 'HIGH' THEN 1 ELSE 0 END) AS HighRisk,
        SUM(CASE WHEN RiskLevel = 'MEDIUM' THEN 1 ELSE 0 END) AS MediumRisk,
        SUM(CASE WHEN RiskLevel = 'LOW' THEN 1 ELSE 0 END) AS LowRisk,
        AVG(FraudScore) AS AvgFraudScore,
        MAX(FraudScore) AS MaxFraudScore
    FROM [dbo].[FraudScores]
    
    UNION ALL
    
    SELECT 
        'Top Risky Nominations' AS ReportSection,
        COUNT(*) AS Count,
        NULL, NULL, NULL, NULL, NULL, NULL
    FROM [dbo].[FraudScores]
    WHERE RiskLevel IN ('CRITICAL', 'HIGH')
    
    -- Return top 20 most suspicious nominations
    SELECT TOP 20
        fs.NominationId,
        fs.FraudScore,
        fs.RiskLevel,
        fs.FraudFlags,
        n.NominatorId,
        n.BeneficiaryId,
        n.ApproverId,
        n.DollarAmount,
        n.NominationDate,
        n.ApprovedDate,
        n.Status
    FROM [dbo].[FraudScores] fs
    INNER JOIN [dbo].[Nominations] n ON fs.NominationId = n.NominationId
    ORDER BY fs.FraudScore DESC, fs.NominationId
    
    DECLARE @TotalAnalyzed INT
	DECLARE @SuspiciousFound INT

	SELECT @TotalAnalyzed = COUNT(*) 
	FROM [dbo].[Nominations] 
	WHERE NominationDate >= @AnalysisStartDate

	SELECT @SuspiciousFound = COUNT(*) 
	FROM [dbo].[FraudScores]

	PRINT 'Fraud detection analysis complete.'
	PRINT 'Total nominations analyzed: ' + CAST(@TotalAnalyzed AS NVARCHAR(20))
	PRINT 'Suspicious nominations found: ' + CAST(@SuspiciousFound AS NVARCHAR(20))		
END
GO

-- Execute the procedure
EXEC [dbo].[sp_DetectFraud] 
    @ThresholdScore = 0,  -- Score all nominations (0 = no threshold)
    @DaysToAnalyze = 365  -- Analyze last year
GO

-- Query to view fraud scores
SELECT 
    fs.NominationId,
    fs.FraudScore,
    fs.RiskLevel,
    fs.FraudFlags,
    n.NominatorId,
    n.BeneficiaryId,
    n.DollarAmount,
    n.Status
FROM [dbo].[FraudScores] fs
INNER JOIN [dbo].[Nominations] n ON fs.NominationId = n.NominationId
ORDER BY fs.FraudScore DESC