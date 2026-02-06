"""
Fraud Detection Dashboard & Reporting
Generates comprehensive fraud analysis reports
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta
import pyodbc
import os
from dotenv import load_dotenv

load_dotenv()

# Set style
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (16, 12)

def get_db_connection():
    """Create database connection
    
    Note: Using pyodbc directly with pandas.read_sql() will generate warnings.
    For production, consider using SQLAlchemy: 
    from sqlalchemy import create_engine
    engine = create_engine('mssql+pyodbc://...')
    """
    connection_string = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.getenv('SQL_SERVER')};"
        f"DATABASE={os.getenv('SQL_DATABASE')};"
        f"UID={os.getenv('SQL_USER')};"
        f"PWD={os.getenv('SQL_PASSWORD')};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no;"
        f"Connection Timeout=30;"
    )
    return pyodbc.connect(connection_string)

def generate_fraud_dashboard():
    """Generate comprehensive fraud detection dashboard"""
    
    print("="*60)
    print("FRAUD DETECTION DASHBOARD")
    print("="*60)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    conn = get_db_connection()
    
    # ========================================================================
    # 1. OVERALL STATISTICS
    # ========================================================================
    
    print("📊 OVERALL STATISTICS")
    print("-" * 60)
    
    query_overall = """
    SELECT 
        COUNT(DISTINCT n.NominationId) as TotalNominations,
        COUNT(DISTINCT fs.NominationId) as FlaggedNominations,
        SUM(CASE WHEN fs.RiskLevel = 'CRITICAL' THEN 1 ELSE 0 END) as CriticalRisk,
        SUM(CASE WHEN fs.RiskLevel = 'HIGH' THEN 1 ELSE 0 END) as HighRisk,
        SUM(CASE WHEN fs.RiskLevel = 'MEDIUM' THEN 1 ELSE 0 END) as MediumRisk,
        SUM(CASE WHEN fs.RiskLevel = 'LOW' THEN 1 ELSE 0 END) as LowRisk,
        AVG(fs.FraudScore) as AvgFraudScore,
        SUM(n.DollarAmount) as TotalDollarsAtRisk
    FROM dbo.Nominations n
    LEFT JOIN dbo.FraudScores fs ON n.NominationId = fs.NominationId
    """
    
    df_overall = pd.read_sql(query_overall, conn)
    
    total = df_overall['TotalNominations'].iloc[0]
    flagged = df_overall['FlaggedNominations'].iloc[0]
    critical = df_overall['CriticalRisk'].iloc[0] or 0
    high = df_overall['HighRisk'].iloc[0] or 0
    medium = df_overall['MediumRisk'].iloc[0] or 0
    low = df_overall['LowRisk'].iloc[0] or 0
    avg_score = df_overall['AvgFraudScore'].iloc[0] or 0
    total_risk = df_overall['TotalDollarsAtRisk'].iloc[0] or 0
    
    print(f"Total Nominations: {total:,}")
    print(f"Flagged for Fraud: {flagged:,} ({flagged/total*100:.2f}%)")
    print(f"  • Critical Risk: {critical:,}")
    print(f"  • High Risk: {high:,}")
    print(f"  • Medium Risk: {medium:,}")
    print(f"  • Low Risk: {low:,}")
    print(f"Average Fraud Score: {avg_score:.2f}")
    print(f"Total $ at Risk: ${total_risk:,.2f}")
    print()
    
    # ========================================================================
    # 2. TOP FRAUD PATTERNS
    # ========================================================================
    
    print("🚨 TOP FRAUD PATTERNS DETECTED")
    print("-" * 60)
    
    query_patterns = """
    SELECT TOP 5
        AnalysisType,
        COUNT(*) as Occurrences,
        AVG(MetricValue) as AvgMetric
    FROM dbo.FraudAnalytics
    GROUP BY AnalysisType
    ORDER BY COUNT(*) DESC
    """
    
    df_patterns = pd.read_sql(query_patterns, conn)
    
    pattern_names = {
        'HighFrequency': 'High Frequency Nominations',
        'RepeatedBeneficiary': 'Repeated Beneficiary Pattern',
        'CircularNomination': 'Circular Nominations',
        'HighDollarAmount': 'Unusually High Amounts',
        'RapidApproval': 'Rapid Approvals',
        'SelfDealingNetwork': 'Self-Dealing Networks'
    }
    
    for _, row in df_patterns.iterrows():
        pattern = pattern_names.get(row['AnalysisType'], row['AnalysisType'])
        print(f"{pattern}: {int(row['Occurrences'])} instances (avg: {row['AvgMetric']:.1f})")
    print()
    
    # ========================================================================
    # 3. TOP RISKY USERS
    # ========================================================================
    
    print("👤 TOP 10 HIGH-RISK NOMINATORS")
    print("-" * 60)
    
    query_risky_users = """
    SELECT TOP 10
        n.NominatorId,
        COUNT(*) as TotalNominations,
        AVG(fs.FraudScore) as AvgFraudScore,
        SUM(CASE WHEN fs.RiskLevel IN ('HIGH', 'CRITICAL') THEN 1 ELSE 0 END) as HighRiskCount,
        SUM(n.DollarAmount) as TotalAmount
    FROM dbo.Nominations n
    INNER JOIN dbo.FraudScores fs ON n.NominationId = fs.NominationId
    WHERE fs.FraudScore > 30
    GROUP BY n.NominatorId
    ORDER BY AVG(fs.FraudScore) DESC, COUNT(*) DESC
    """
    
    df_risky_users = pd.read_sql(query_risky_users, conn)
    
    if not df_risky_users.empty:
        print(f"{'UserID':<10} {'Nominations':<12} {'Avg Score':<12} {'High Risk':<12} {'Total $':<12}")
        print("-" * 60)
        for _, row in df_risky_users.iterrows():
            print(f"{row['NominatorId']:<10} {int(row['TotalNominations']):<12} "
                  f"{row['AvgFraudScore']:.1f}{'':<9} {int(row['HighRiskCount']):<12} "
                  f"${row['TotalAmount']:,.0f}")
    else:
        print("No high-risk users found.")
    print()
    
    # ========================================================================
    # 4. TEMPORAL ANALYSIS
    # ========================================================================
    
    print("📅 FRAUD TRENDS OVER TIME")
    print("-" * 60)
    
    query_temporal = """
    SELECT 
        DATEPART(YEAR, n.NominationDate) as Year,
        DATEPART(MONTH, n.NominationDate) as Month,
        COUNT(*) as TotalNominations,
        SUM(CASE WHEN fs.RiskLevel IN ('HIGH', 'CRITICAL') THEN 1 ELSE 0 END) as HighRiskCount,
        AVG(fs.FraudScore) as AvgFraudScore
    FROM dbo.Nominations n
    LEFT JOIN dbo.FraudScores fs ON n.NominationId = fs.NominationId
    WHERE n.NominationDate >= DATEADD(MONTH, -6, GETDATE())
    GROUP BY DATEPART(YEAR, n.NominationDate), DATEPART(MONTH, n.NominationDate)
    ORDER BY Year DESC, Month DESC
    """
    
    df_temporal = pd.read_sql(query_temporal, conn)
    
    if not df_temporal.empty:
        for _, row in df_temporal.head(6).iterrows():
            month_name = datetime(2025, int(row['Month']), 1).strftime('%b')
            fraud_rate = (row['HighRiskCount'] / row['TotalNominations'] * 100) if row['TotalNominations'] > 0 else 0
            print(f"{month_name} {int(row['Year'])}: {int(row['TotalNominations'])} nominations, "
                  f"{int(row['HighRiskCount'])} high-risk ({fraud_rate:.1f}%), "
                  f"avg score: {row['AvgFraudScore']:.1f}")
    print()
    
    # ========================================================================
    # 5. GENERATE VISUALIZATIONS
    # ========================================================================
    
    print("📈 Generating visualizations...")
    
    fig, axes = plt.subplots(3, 2, figsize=(16, 14))
    fig.suptitle('Fraud Detection Dashboard', fontsize=16, fontweight='bold')
    
    # Plot 1: Risk Level Distribution
    risk_data = [critical, high, medium, low]
    risk_labels = ['Critical', 'High', 'Medium', 'Low']
    colors = ['#d32f2f', '#f57c00', '#fbc02d', '#689f38']
    
    axes[0, 0].pie(risk_data, labels=risk_labels, autopct='%1.1f%%', colors=colors, startangle=90)
    axes[0, 0].set_title('Fraud Risk Distribution')
    
    # Plot 2: Fraud Patterns
    if not df_patterns.empty:
        pattern_labels = [pattern_names.get(p, p)[:20] for p in df_patterns['AnalysisType']]
        axes[0, 1].barh(pattern_labels, df_patterns['Occurrences'], color='#1976d2')
        axes[0, 1].set_xlabel('Number of Occurrences')
        axes[0, 1].set_title('Top Fraud Patterns Detected')
        axes[0, 1].invert_yaxis()
    
    # Plot 3: Fraud Score Distribution
    query_scores = "SELECT FraudScore FROM dbo.FraudScores WHERE FraudScore > 0"
    df_scores = pd.read_sql(query_scores, conn)
    
    if not df_scores.empty:
        axes[1, 0].hist(df_scores['FraudScore'], bins=20, color='#1976d2', edgecolor='black', alpha=0.7)
        axes[1, 0].axvline(50, color='red', linestyle='--', label='High Risk Threshold')
        axes[1, 0].set_xlabel('Fraud Score')
        axes[1, 0].set_ylabel('Number of Nominations')
        axes[1, 0].set_title('Fraud Score Distribution')
        axes[1, 0].legend()
    
    # Plot 4: Amount vs Fraud Score
    query_amount = """
    SELECT n.DollarAmount, fs.FraudScore
    FROM dbo.Nominations n
    INNER JOIN dbo.FraudScores fs ON n.NominationId = fs.NominationId
    """
    df_amount = pd.read_sql(query_amount, conn)
    
    if not df_amount.empty:
        axes[1, 1].scatter(df_amount['DollarAmount'], df_amount['FraudScore'], 
                          alpha=0.5, c=df_amount['FraudScore'], cmap='RdYlGn_r', s=20)
        axes[1, 1].set_xlabel('Dollar Amount ($)')
        axes[1, 1].set_ylabel('Fraud Score')
        axes[1, 1].set_title('Amount vs Fraud Score')
        axes[1, 1].axhline(50, color='red', linestyle='--', alpha=0.5)
    
    # Plot 5: Temporal Trends
    if not df_temporal.empty:
        df_temporal['MonthYear'] = df_temporal.apply(
            lambda x: f"{datetime(2025, int(x['Month']), 1).strftime('%b')} '{str(int(x['Year']))[-2:]}", 
            axis=1
        )
        
        ax5_twin = axes[2, 0].twinx()
        axes[2, 0].bar(range(len(df_temporal)), df_temporal['TotalNominations'], 
                       alpha=0.6, color='#1976d2', label='Total Nominations')
        ax5_twin.plot(range(len(df_temporal)), df_temporal['AvgFraudScore'], 
                     color='#d32f2f', marker='o', linewidth=2, label='Avg Fraud Score')
        
        axes[2, 0].set_xlabel('Month')
        axes[2, 0].set_ylabel('Total Nominations', color='#1976d2')
        ax5_twin.set_ylabel('Avg Fraud Score', color='#d32f2f')
        axes[2, 0].set_title('Nomination Volume & Fraud Trends')
        axes[2, 0].set_xticks(range(len(df_temporal)))
        axes[2, 0].set_xticklabels(df_temporal['MonthYear'], rotation=45)
        axes[2, 0].legend(loc='upper left')
        ax5_twin.legend(loc='upper right')
    
    # Plot 6: Top Risky Users
    if not df_risky_users.empty:
        top_users = df_risky_users.head(10)
        axes[2, 1].barh(range(len(top_users)), top_users['AvgFraudScore'], color='#d32f2f')
        axes[2, 1].set_yticks(range(len(top_users)))
        axes[2, 1].set_yticklabels([f"User {int(uid)}" for uid in top_users['NominatorId']])
        axes[2, 1].set_xlabel('Average Fraud Score')
        axes[2, 1].set_title('Top 10 High-Risk Users')
        axes[2, 1].invert_yaxis()
        axes[2, 1].axvline(50, color='orange', linestyle='--', alpha=0.5, label='High Risk')
        axes[2, 1].axvline(70, color='red', linestyle='--', alpha=0.5, label='Critical Risk')
        axes[2, 1].legend()
    
    plt.tight_layout()
    
    # Save dashboard
    filename = f'fraud_dashboard_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
    plt.savefig(filename, dpi=300, bbox_inches='tight')
    print(f"✓ Dashboard saved to '{filename}'")
    
    plt.close()
    conn.close()
    
    print()
    print("="*60)
    print("DASHBOARD GENERATION COMPLETE!")
    print("="*60)
    print(f"\nReview the generated image: {filename}")
    print("Next steps:")
    print("1. Review high-risk users and investigate patterns")
    print("2. Update fraud detection rules if needed")
    print("3. Retrain ML model with new fraud labels")
    print("4. Schedule regular fraud audits")

if __name__ == "__main__":
    generate_fraud_dashboard()