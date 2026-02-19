import React, { useState, useEffect } from 'react';
import { AlertCircle, TrendingUp, Users, DollarSign, Clock, AlertTriangle, BarChart3 } from 'lucide-react';
import { useImpersonation } from '../contexts/ImpersonationContext';
import { getAccessToken } from '../services/api';

interface AnalyticsOverview {
  totalNominationsAllTime: number;
  totalAmountSpent: number;
  approvedNominations: number;
  pendingNominations: number;
  averageAwardAmount: number;
  rejectionRate: number;
  fraudAlertsThisMonth: number;
}

interface SpendingTrend {
  date: string;
  nominationCount: number;
  amount: number;
}

interface DepartmentSpending {
  departmentName: string;
  nominationCount: number;
  totalSpent: number;
  averageAmount: number;
}

interface TopRecipient {
  UserId: number;
  FirstName: string;
  LastName: string;
  nominationCount: number;
  totalAmount: number;
}

interface FraudAlert {
  NominationId: number;
  riskLevel: string;
  fraudScore: number;
  flags: string[];
  nominatorName: string;
  beneficiaryName: string;
  amount: number;
  nominationDate: string;
}

interface ApprovalMetrics {
  totalNominations: number;
  approvedCount: number;
  rejectedCount: number;
  avgDaysToApproval: number;
  approvalRate: number;
}

interface DiversityMetrics {
  uniqueRecipients: number;
  totalNominations: number;
  avgNominationsPerRecipient: number;
  giniCoefficient: number;
  topRecipientPercent: number;
}

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const AnalyticsDashboard: React.FC = () => {
  const { impersonatedUser } = useImpersonation();
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [trends, setTrends] = useState<SpendingTrend[]>([]);
  const [departments, setDepartments] = useState<DepartmentSpending[]>([]);
  const [topRecipients, setTopRecipients] = useState<TopRecipient[]>([]);
  const [topNominators, setTopNominators] = useState<TopRecipient[]>([]);
  const [fraudAlerts, setFraudAlerts] = useState<FraudAlert[]>([]);
  const [approvalMetrics, setApprovalMetrics] = useState<ApprovalMetrics | null>(null);
  const [diversityMetrics, setDiversityMetrics] = useState<DiversityMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<'overview' | 'spending' | 'fraud' | 'diversity'>('overview');

  useEffect(() => {
    fetchAnalytics();
    // Refresh every 5 minutes
    const interval = setInterval(fetchAnalytics, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  const apiFetch = async <T,>(path: string): Promise<T> => {
    const token = await getAccessToken();
    const headers = new Headers({
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    });
    
    if (impersonatedUser) {
      headers.set('X-Impersonate-User', impersonatedUser);
    }

    const res = await fetch(`${API_BASE_URL}${path}`, { headers });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return res.json();
  };

  const fetchAnalytics = async () => {
    try {
      setLoading(true);
      const [ovData, trendsData, deptData, topRecData, topNomData, fraudData, approvalData, divData] = 
        await Promise.all([
          apiFetch<AnalyticsOverview>('/api/admin/analytics/overview'),
          apiFetch<SpendingTrend[]>('/api/admin/analytics/spending-trends?days=90'),
          apiFetch<DepartmentSpending[]>('/api/admin/analytics/department-spending'),
          apiFetch<TopRecipient[]>('/api/admin/analytics/top-recipients?limit=10'),
          apiFetch<TopRecipient[]>('/api/admin/analytics/top-nominators?limit=10'),
          apiFetch<FraudAlert[]>('/api/admin/analytics/fraud-alerts?limit=20'),
          apiFetch<ApprovalMetrics>('/api/admin/analytics/approval-metrics'),
          apiFetch<DiversityMetrics>('/api/admin/analytics/diversity-metrics')
        ]);

      setOverview(ovData);
      setTrends(trendsData);
      setDepartments(deptData);
      setTopRecipients(topRecData);
      setTopNominators(topNomData);
      setFraudAlerts(fraudData);
      setApprovalMetrics(approvalData);
      setDiversityMetrics(divData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  };

  if (loading && !overview) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
        <div className="flex items-center gap-2">
          <AlertCircle className="text-red-600" />
          <span className="text-red-700">{error}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Tab Navigation */}
      <div className="flex gap-2 border-b border-gray-200">
        {[
          { id: 'overview', label: 'Overview', icon: BarChart3 },
          { id: 'spending', label: 'Spending Trends', icon: TrendingUp },
          { id: 'fraud', label: 'Fraud Alerts', icon: AlertTriangle },
          { id: 'diversity', label: 'Diversity Metrics', icon: Users }
        ].map(tab => {
          const TabIcon = tab.icon;
          const isActive = selectedTab === (tab.id as any);
          return (
            <button
              key={tab.id}
              onClick={() => setSelectedTab(tab.id as any)}
              className={`flex items-center gap-2 px-4 py-3 font-medium transition-colors ${
                isActive 
                  ? 'text-blue-600 border-b-2 border-blue-600' 
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              <TabIcon size={18} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Overview Tab */}
      {selectedTab === 'overview' && overview && (
        <div className="space-y-6">
          {/* Key Metrics Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard
              icon={DollarSign}
              label="Total Spent"
              value={`$${(overview.totalAmountSpent / 1000).toFixed(1)}K`}
              change="+12% vs last month"
              positive
            />
            <MetricCard
              icon={TrendingUp}
              label="Total Nominations"
              value={overview.totalNominationsAllTime.toString()}
              change={`${overview.approvedNominations} approved`}
              positive
            />
            <MetricCard
              icon={Clock}
              label="Pending Approvals"
              value={overview.pendingNominations.toString()}
              change={`Avg award: $${Math.round(overview.averageAwardAmount)}`}
            />
            <MetricCard
              icon={AlertTriangle}
              label="Fraud Alerts"
              value={overview.fraudAlertsThisMonth.toString()}
              change={`Rejection rate: ${(overview.rejectionRate * 100).toFixed(1)}%`}
              warning={overview.fraudAlertsThisMonth > 0}
            />
          </div>

          {/* Department Spending */}
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <BarChart3 size={20} />
              Department Spending Breakdown
            </h2>
            <DepartmentTable departments={departments} />
          </div>

          {/* Top Recipients and Nominators */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="text-lg font-semibold mb-4">Top Recipients</h3>
              <RecipientList recipients={topRecipients} />
            </div>
            <div className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="text-lg font-semibold mb-4">Top Nominators</h3>
              <RecipientList recipients={topNominators} />
            </div>
          </div>

          {/* Approval Metrics */}
          {approvalMetrics && (
            <div className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="text-lg font-semibold mb-4">Approval Metrics</h3>
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div>
                  <p className="text-gray-600 text-sm">Total Nominations</p>
                  <p className="text-2xl font-bold">{approvalMetrics.totalNominations}</p>
                </div>
                <div>
                  <p className="text-gray-600 text-sm">Approval Rate</p>
                  <p className="text-2xl font-bold">{(approvalMetrics.approvalRate * 100).toFixed(1)}%</p>
                </div>
                <div>
                  <p className="text-gray-600 text-sm">Avg Days to Approval</p>
                  <p className="text-2xl font-bold">{approvalMetrics.avgDaysToApproval.toFixed(1)}</p>
                </div>
                <div>
                  <p className="text-gray-600 text-sm">Rejected</p>
                  <p className="text-2xl font-bold text-red-600">{approvalMetrics.rejectedCount}</p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Spending Trends Tab */}
      {selectedTab === 'spending' && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">90-Day Spending Trends</h2>
          <SpendingTrendChart trends={trends} />
        </div>
      )}

      {/* Fraud Alerts Tab */}
      {selectedTab === 'fraud' && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <AlertTriangle size={20} className="text-red-600" />
            Recent Fraud Alerts
          </h2>
          <FraudAlertsList alerts={fraudAlerts} />
        </div>
      )}

      {/* Diversity Metrics Tab */}
      {selectedTab === 'diversity' && diversityMetrics && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="text-lg font-semibold mb-4">Award Distribution Balance</h3>
              <div className="space-y-4">
                <div>
                  <p className="text-gray-600 text-sm">Unique Recipients</p>
                  <p className="text-3xl font-bold">{diversityMetrics.uniqueRecipients}</p>
                  <p className="text-xs text-gray-500">out of {diversityMetrics.totalNominations} total awards</p>
                </div>
                <div>
                  <p className="text-gray-600 text-sm">Avg Awards Per Recipient</p>
                  <p className="text-3xl font-bold">{diversityMetrics.avgNominationsPerRecipient.toFixed(2)}</p>
                </div>
              </div>
            </div>

            <div className="bg-white rounded-lg border border-gray-200 p-6">
              <h3 className="text-lg font-semibold mb-4">Equality Index (Gini)</h3>
              <div className="space-y-4">
                <div>
                  <p className="text-gray-600 text-sm">Gini Coefficient</p>
                  <p className="text-3xl font-bold">{diversityMetrics.giniCoefficient.toFixed(3)}</p>
                  <p className="text-xs text-gray-500">0 = perfect equality, 1 = perfect inequality</p>
                </div>
                <div>
                  <p className="text-gray-600 text-sm">Top Recipient Share</p>
                  <p className="text-3xl font-bold text-orange-600">{diversityMetrics.topRecipientPercent.toFixed(1)}%</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

interface MetricCardProps {
  icon: React.ComponentType<{ size: number }>;
  label: string;
  value: string;
  change: string;
  positive?: boolean;
  warning?: boolean;
}

const MetricCard: React.FC<MetricCardProps> = ({ icon: Icon, label, value, change, positive, warning }) => (
  <div className="bg-white rounded-lg border border-gray-200 p-6">
    <div className="flex items-start justify-between">
      <div>
        <p className="text-gray-600 text-sm font-medium">{label}</p>
        <p className="text-3xl font-bold mt-2">{value}</p>
        <p className={`text-xs mt-2 ${positive ? 'text-green-600' : warning ? 'text-red-600' : 'text-gray-600'}`}>
          {change}
        </p>
      </div>
      <Icon size={24} className={positive ? 'text-green-600' : warning ? 'text-red-600' : 'text-blue-600'} />
    </div>
  </div>
);

interface DepartmentTableProps {
  departments: DepartmentSpending[];
}

const DepartmentTable: React.FC<DepartmentTableProps> = ({ departments }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-sm">
      <thead className="bg-gray-50 border-b">
        <tr>
          <th className="text-left px-4 py-3 font-semibold">Department</th>
          <th className="text-right px-4 py-3 font-semibold">Awards</th>
          <th className="text-right px-4 py-3 font-semibold">Total Spent</th>
          <th className="text-right px-4 py-3 font-semibold">Avg Award</th>
        </tr>
      </thead>
      <tbody className="divide-y">
        {departments.map((dept, i) => (
          <tr key={i} className="hover:bg-gray-50">
            <td className="px-4 py-3">{dept.departmentName}</td>
            <td className="text-right px-4 py-3">{dept.nominationCount}</td>
            <td className="text-right px-4 py-3 font-semibold">${dept.totalSpent.toLocaleString()}</td>
            <td className="text-right px-4 py-3">${Math.round(dept.averageAmount).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

interface RecipientListProps {
  recipients: TopRecipient[];
}

const RecipientList: React.FC<RecipientListProps> = ({ recipients }) => (
  <div className="space-y-3">
    {recipients.map((person, i) => (
      <div key={i} className="flex items-center justify-between p-3 bg-gray-50 rounded">
        <div>
          <p className="font-medium">{person.FirstName} {person.LastName}</p>
          <p className="text-xs text-gray-600">{person.nominationCount} awards</p>
        </div>
        <p className="font-semibold">${person.totalAmount.toLocaleString()}</p>
      </div>
    ))}
  </div>
);

interface SpendingTrendChartProps {
  trends: SpendingTrend[];
}

const SpendingTrendChart: React.FC<SpendingTrendChartProps> = ({ trends }) => {
  const maxAmount = Math.max(...trends.map(t => t.amount), 1);
  const sorted = [...trends].reverse();

  return (
    <div className="space-y-4">
      <div className="h-64 flex items-end gap-1 border-l border-b border-gray-300 p-4">
        {sorted.slice(0, 30).map((trend, i) => (
          <div
            key={i}
            className="flex-1 bg-blue-500 rounded-t hover:bg-blue-600 transition-colors relative group"
            style={{
              height: `${(trend.amount / maxAmount) * 100}%`,
              minHeight: '4px'
            }}
            title={`${trend.date}: $${trend.amount.toLocaleString()}`}
          >
            <div className="opacity-0 group-hover:opacity-100 absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 bg-gray-900 text-white text-xs px-2 py-1 rounded whitespace-nowrap">
              ${trend.amount.toLocaleString()}
            </div>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-600 text-center">Last 30 days</p>
    </div>
  );
};

interface FraudAlertsListProps {
  alerts: FraudAlert[];
}

const FraudAlertsList: React.FC<FraudAlertsListProps> = ({ alerts }) => {
  if (!alerts.length) {
    return <p className="text-center text-gray-600 py-8">No fraud alerts detected</p>;
  }

  return (
    <div className="space-y-3">
      {alerts.map((alert, i) => (
        <div key={i} className={`p-4 rounded-lg border-2 ${
          alert.riskLevel === 'High' ? 'bg-red-50 border-red-300' : 'bg-yellow-50 border-yellow-300'
        }`}>
          <div className="flex items-start justify-between mb-2">
            <div>
              <p className="font-semibold">
                {alert.nominatorName} → {alert.beneficiaryName}
              </p>
              <p className="text-sm text-gray-600">${alert.amount.toLocaleString()} on {alert.nominationDate}</p>
            </div>
            <span className={`px-3 py-1 rounded-full text-sm font-semibold ${
              alert.riskLevel === 'High' 
                ? 'bg-red-200 text-red-800' 
                : 'bg-yellow-200 text-yellow-800'
            }`}>
              {alert.riskLevel}
            </span>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-gray-600">
              <span className="font-semibold">Score:</span> {alert.fraudScore}/100
            </p>
            {alert.flags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {alert.flags.map((flag, j) => (
                  <span key={j} className="text-xs bg-gray-200 text-gray-800 px-2 py-1 rounded">
                    {flag}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
