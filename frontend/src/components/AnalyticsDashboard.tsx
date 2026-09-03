import React, { useState, useEffect } from 'react';
import { AlertCircle, TrendingUp, Users, DollarSign, Clock, AlertTriangle, BarChart3, Send, ShieldAlert, ChevronDown, RefreshCw, Download, LineChart } from 'lucide-react';
import { useImpersonation } from '../contexts/ImpersonationContext';
import { useTenantConfig } from '../contexts/TenantConfigContext';
import { useTranslation } from 'react-i18next';
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

interface CategoryBreakdown {
  categoryDescription: string;
  nominationCount: number;
  totalAmount: number;
  avgAmount: number;
}

type AnalyticsTab =
  | 'overview'
  | 'spending'
  | 'fraud'
  | 'diversity'
  | 'ask'
  | 'integrity'
  | 'forecast';

interface IntegrityRun {
  runId: string;
  runDate: string;
  totalFindings: number;
}

interface IntegrityFinding {
  findingId: number;
  patternType: string;
  severity: string;
  affectedUsers: string;   // JSON array string
  nominationIds: string;   // JSON array string
  detail: string;
  detectedAt: string;
  totalAmount?: number;
  findingScore?: number | null;
  scoringPolicyVersion?: number | null;
}

// Human-readable labels and icons per pattern type
const PATTERN_META: Record<string, { label: string; description: string }> = {
  Ring:                { label: 'Nomination Ring',        description: 'Directed cycle of mutual nominations' },
  BipartiteDenseBlock: { label: 'Bipartite Dense Block', description: 'Dense many-to-few or few-to-many nomination campaign' },
  TemporalBurst:       { label: 'Temporal Burst',         description: 'Anomalous nomination volume compressed into a short window' },
  SuperNominator:      { label: 'Super Nominator',        description: 'Unusually high nomination volume' },
  SuperBeneficiary:    { label: 'Super Beneficiary',      description: 'Unusually frequent beneficiary with broad nominator support' },
  Desert:              { label: 'Nomination Desert',      description: 'Entire team absent from all nominations' },
  ApproverAffinity:    { label: 'Approver Affinity (legacy)', description: 'Historical finding retained for audit only' },
  CopyPaste:           { label: 'Copy-Paste Fraud',       description: 'Near-identical nomination descriptions' },
  HiddenCandidate:     { label: 'Hidden Candidate',       description: 'Named in descriptions but never nominated' },
};

const SEVERITY_STYLES: Record<string, { card: string; badge: string }> = {
  Critical: { card: 'bg-red-50 border-red-300',    badge: 'bg-red-200 text-red-800' },
  High:     { card: 'bg-orange-50 border-orange-300', badge: 'bg-orange-200 text-orange-800' },
  Medium:   { card: 'bg-yellow-50 border-yellow-300', badge: 'bg-yellow-200 text-yellow-800' },
  Low:      { card: 'bg-blue-50 border-blue-300',   badge: 'bg-blue-200 text-blue-800' },
};

interface ForecastWeek {
  weekStart: string;
  weekIndex: number;
  projectedNominations: number;
  projectedNominationsLower: number;
  projectedNominationsUpper: number;
  projectedReviews: number;
  projectedReviewsLower: number;
  projectedReviewsUpper: number;
  projectedQueueDepth: number;
  projectedQueueDepthLower: number;
  projectedQueueDepthUpper: number;
}
interface ForecastHistoryWeek { weekStart: string; nominations: number; reviews: number; }
interface BudgetCumulativePoint {
  weekStart: string;
  actual: number | null;
  projected: number | null;
  lower: number | null;
  upper: number | null;
}
interface ForecastSeriesPoint { weekStart?: string; date?: string; point: number; lower: number; upper: number; model?: string; }
interface DeptSeriesPoint { weekStart: string; point: number; lower: number; upper: number; }
interface DepartmentForecast {
  title: string;
  nominationsModel?: string;
  spendModel?: string;
  nominations: DeptSeriesPoint[];
  spend: DeptSeriesPoint[];
}
interface ModelMetric { MASE: number | null; sMAPE: number | null; RMSE: number | null; coverage: number | null; folds: number; }
interface ForecastResponse {
  generatedAt: string;
  horizonWeeks: number;
  historyDays: number;
  confidence: number;
  source?: string;
  runId?: string | null;
  modelComparison?: {
    nominations_total?: Record<string, ModelMetric | string>;
    spend_total?: Record<string, ModelMetric | string>;
    departments?: Record<string, Record<string, number>>;
  } | null;
  forecasts?: {
    nominationsWeekly: ForecastSeriesPoint[];
    spendWeekly: ForecastSeriesPoint[];
    nominationsDaily: ForecastSeriesPoint[];
    spendHistory?: { weekStart: string; amount: number }[];
    departments: DepartmentForecast[];
  } | null;
  inputs: {
    reviewRate: number;
    reviewRateIsDefault: boolean;
    flaggedNominations: number;
    totalNominationsWindow: number;
    avgDaysToApproval: number;
    avgDaysToApprovalIsDefault: boolean;
    weeklyObservations: number;
    seasonalityUsed: boolean;
    note: string;
  };
  reviewLoad: {
    history: ForecastHistoryWeek[];
    forecast: ForecastWeek[];
    model: { name: string; alpha: number; beta: number; residualSigma: number; weeklyObservations: number; degradedToFlat: boolean; };
  };
  budgetPacing: {
    annualBudget: number;
    fiscalYearStart: string;
    spentToDate: number;
    projectedHorizonSpend: number;
    projectedHorizonLower: number;
    projectedHorizonUpper: number;
    budgetUtilizationAtHorizon: number | null;
    exhaustionDate: string | null;
    exhaustionDateEarliest: string | null;
    exhaustionDateLatest: string | null;
    cumulative: BudgetCumulativePoint[];
  } | null;
}

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface AnalyticsDashboardProps {
  onOpenNominationLogs: (nominationId: number) => void;
}

export const AnalyticsDashboard: React.FC<AnalyticsDashboardProps> = ({ onOpenNominationLogs }) => {
  const { impersonatedUser } = useImpersonation();
  const { formatCurrency } = useTenantConfig();   // tenant locale + currency aware
  const { t } = useTranslation();
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [trends, setTrends] = useState<SpendingTrend[]>([]);
  const [departments, setDepartments] = useState<DepartmentSpending[]>([]);
  const [topRecipients, setTopRecipients] = useState<TopRecipient[]>([]);
  const [topNominators, setTopNominators] = useState<TopRecipient[]>([]);
  const [fraudAlerts, setFraudAlerts] = useState<FraudAlert[]>([]);
  const [approvalMetrics, setApprovalMetrics] = useState<ApprovalMetrics | null>(null);
  const [diversityMetrics, setDiversityMetrics] = useState<DiversityMetrics | null>(null);
  const [categoryBreakdown, setCategoryBreakdown] = useState<CategoryBreakdown[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<AnalyticsTab>('ask');
  const [forecast, setForecast] = useState<ForecastResponse | null>(null);
  const [forecastLoading, setForecastLoading] = useState(false);
  // Annual recognition budget for the pacing projection (admin-supplied). Blank = pacing omitted.
  const [budgetInput, setBudgetInput] = useState<string>('');
  const [appliedBudget, setAppliedBudget] = useState<number | null>(null);
  const [integrityRuns, setIntegrityRuns] = useState<IntegrityRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [integrityFindings, setIntegrityFindings] = useState<IntegrityFinding[]>([]);
  const [integrityLoading, setIntegrityLoading] = useState(false);
  const [expandedFinding, setExpandedFinding] = useState<number | null>(null);
  const [activePatternFilters, setActivePatternFilters] = useState<Set<string>>(new Set());
  const [activeSeverityFilters, setActiveSeverityFilters] = useState<Set<string>>(new Set());
  
  // Track which tabs have been loaded to avoid refetching
  const [loadedTabs, setLoadedTabs] = useState<Set<string>>(new Set(['ask']));
  
  // AI chat state
  const [aiQuestion, setAiQuestion] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<Array<{
    role: 'user' | 'assistant';
    content: string;
    export?: { format: string; file_size: number; label: string; filename: string; download_url: string; };
  }>>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const activeConversationRef = React.useRef<string | null>(null); // always current, safe in async closures
  const [conversations, setConversations] = useState<Array<{
    conversationId: string; title: string; updatedAt: string;
  }>>([]);
  const [convLoading, setConvLoading] = useState(false);
  const [editingConvId, setEditingConvId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const chatEndRef = React.useRef<HTMLDivElement>(null);
  const questionInputRef = React.useRef<HTMLTextAreaElement>(null);
  // Investigate mode — one-shot: resets to false after each submission
  const [useOrchestrator, setUseOrchestrator] = React.useState(false);

  // Auto-resize the textarea whenever the question text changes
  React.useEffect(() => {
    const el = questionInputRef.current;
    if (!el) return;
    el.style.height = 'auto';          // shrink first so scrollHeight is accurate
    el.style.height = `${el.scrollHeight}px`;
  }, [aiQuestion]);

  useEffect(() => {
    // Don't fetch on mount - wait for tab selection
    // Refresh 'ask' tab doesn't need data, so we skip auto-refresh
  }, []);

  const apiFetch = async <T,>(path: string): Promise<T> => {
    const token = await getAccessToken();
    const headers = new Headers({
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    });
    
    if (impersonatedUser && typeof impersonatedUser === 'string') {
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
      const [ovData, trendsData, deptData, topRecData, topNomData, fraudData, approvalData, divData, catData] =
        await Promise.all([
          apiFetch<AnalyticsOverview>('/api/admin/analytics/overview'),
          apiFetch<SpendingTrend[]>('/api/admin/analytics/spending-trends?days=30'),
          apiFetch<DepartmentSpending[]>('/api/admin/analytics/department-spending'),
          apiFetch<TopRecipient[]>('/api/admin/analytics/top-recipients?limit=10'),
          apiFetch<TopRecipient[]>('/api/admin/analytics/top-nominators?limit=10'),
          apiFetch<FraudAlert[]>('/api/admin/analytics/fraud-alerts?limit=20'),
          apiFetch<ApprovalMetrics>('/api/admin/analytics/approval-metrics'),
          apiFetch<DiversityMetrics>('/api/admin/analytics/diversity-metrics'),
          apiFetch<CategoryBreakdown[]>('/api/admin/analytics/category-breakdown'),
        ]);

      setOverview(ovData);
      setTrends(trendsData);
      setDepartments(deptData);
      setTopRecipients(topRecData);
      setTopNominators(topNomData);
      setFraudAlerts(fraudData);
      setApprovalMetrics(approvalData);
      setDiversityMetrics(divData);
      setCategoryBreakdown(catData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  };

  const fetchForecast = async (budget?: number | null) => {
    setForecastLoading(true);
    try {
      const params = new URLSearchParams({ weeks: '8', history_days: '180', confidence: '0.8' });
      if (budget && budget > 0) params.set('annual_budget', String(budget));
      const data = await apiFetch<ForecastResponse>(`/api/admin/analytics/forecast?${params.toString()}`);
      setForecast(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load forecast');
    } finally {
      setForecastLoading(false);
    }
  };

  const applyBudget = async () => {
    const parsed = parseFloat(budgetInput.replace(/[^0-9.]/g, ''));
    const budget = isNaN(parsed) || parsed <= 0 ? null : parsed;
    setAppliedBudget(budget);
    await fetchForecast(budget);
  };

  const fetchIntegrityRuns = async () => {
    setIntegrityLoading(true);
    setActivePatternFilters(new Set());
    setActiveSeverityFilters(new Set());
    try {
      const runs = await apiFetch<IntegrityRun[]>('/api/admin/analytics/integrity/runs');
      setIntegrityRuns(runs);
      if (runs.length > 0) {
        setSelectedRunId(runs[0].runId);
        const findings = await apiFetch<IntegrityFinding[]>(
          `/api/admin/analytics/integrity/findings?run_id=${runs[0].runId}`
        );
        setIntegrityFindings(findings);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load integrity data');
    } finally {
      setIntegrityLoading(false);
    }
  };

  const handleRunChange = async (runId: string) => {
    setSelectedRunId(runId);
    setActivePatternFilters(new Set());
    setActiveSeverityFilters(new Set());
    setExpandedFinding(null);
    setIntegrityLoading(true);
    try {
      const findings = await apiFetch<IntegrityFinding[]>(
        `/api/admin/analytics/integrity/findings?run_id=${runId}`
      );
      setIntegrityFindings(findings);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load findings');
    } finally {
      setIntegrityLoading(false);
    }
  };

  const exportFinding = async (findingId: number, e: React.MouseEvent) => {
    e.stopPropagation();  // don't toggle the accordion
    try {
      const token = await getAccessToken();
      const headers = new Headers({ 'Authorization': `Bearer ${token}` });
      if (impersonatedUser && typeof impersonatedUser === 'string')
        headers.set('X-Impersonate-User', impersonatedUser);
      const res = await fetch(
        `${API_BASE_URL}/api/admin/analytics/integrity/findings/${findingId}/export`,
        { headers }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href     = url;
      a.download = `finding_${findingId}_export.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(`Export failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  // Handle tab selection with lazy loading
  const handleTabChange = async (tabId: AnalyticsTab) => {
    setSelectedTab(tabId);
    
    // Integrity tab has its own fetch path
    if (tabId === 'integrity') {
      if (!loadedTabs.has('integrity')) {
        await fetchIntegrityRuns();
        setLoadedTabs(prev => new Set([...prev, 'integrity']));
      }
      return;
    }

    // Forecast tab has its own fetch path
    if (tabId === 'forecast') {
      if (!loadedTabs.has('forecast')) {
        await fetchForecast(appliedBudget);
        setLoadedTabs(prev => new Set([...prev, 'forecast']));
      }
      return;
    }

    // If this tab hasn't been loaded yet, fetch its data
    if (!loadedTabs.has(tabId) && tabId !== 'ask') {
      try {
        setLoading(true);
        await fetchAnalytics();
        setLoadedTabs(prev => new Set([...prev, tabId]));
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load analytics');
      } finally {
        setLoading(false);
      }
    }
  };

  const fetchConversations = async () => {
    setConvLoading(true);
    try {
      const data = await apiFetch<Array<{ conversationId: string; title: string; updatedAt: string; }>>(
        '/api/admin/analytics/conversations'
      );
      setConversations(data);
    } catch { /* silently ignore */ }
    finally { setConvLoading(false); }
  };

  const loadConversation = async (conversationId: string) => {
    try {
      const messages = await apiFetch<Array<{ role: string; content: string; exportJson?: string; }>>(
        `/api/admin/analytics/conversations/${conversationId}/messages`
      );
      setChatMessages(messages.map(m => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        ...(m.exportJson ? { export: JSON.parse(m.exportJson) } : {}),
      })));
      activeConversationRef.current = conversationId;
      setActiveConversationId(conversationId);
      setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    } catch { /* ignore */ }
  };

  const deleteConversation = async (conversationId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const token = await getAccessToken();
      const headers = new Headers({ 'Authorization': `Bearer ${token}` });
      if (impersonatedUser && typeof impersonatedUser === 'string')
        headers.set('X-Impersonate-User', impersonatedUser);
      await fetch(`${API_BASE_URL}/api/admin/analytics/conversations/${conversationId}`, { method: 'DELETE', headers });
      setConversations(prev => prev.filter(c => c.conversationId !== conversationId));
      if (activeConversationRef.current === conversationId) {
        activeConversationRef.current = null;
        setActiveConversationId(null);
        setChatMessages([]);
      }
    } catch { /* ignore */ }
  };

  const renameConversation = async (conversationId: string, newTitle: string) => {
    const trimmed = newTitle.trim();
    setEditingConvId(null);
    if (!trimmed) return;
    // Optimistic update
    setConversations(prev => prev.map(c =>
      c.conversationId === conversationId ? { ...c, title: trimmed } : c
    ));
    try {
      const token = await getAccessToken();
      const headers = new Headers({
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      });
      if (impersonatedUser && typeof impersonatedUser === 'string')
        headers.set('X-Impersonate-User', impersonatedUser);
      const res = await fetch(`${API_BASE_URL}/api/admin/analytics/conversations/${conversationId}`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify({ title: trimmed }),
      });
      if (!res.ok) {
        console.error(`Rename failed: HTTP ${res.status}`);
      }
    } catch (err) {
      console.error('Rename conversation error:', err);
    }
  };

  const startNewConversation = () => {
    activeConversationRef.current = null;
    setActiveConversationId(null);
    setChatMessages([]);
    setAiQuestion('');
  };

  // Reload conversation list every time the Ask tab is opened.
  // No length guard — if the first mount attempt failed (auth not yet ready),
  // switching away and back will retry automatically.
  React.useEffect(() => {
    if (selectedTab === 'ask') {
      fetchConversations();
    }
  }, [selectedTab]);

  const handleAskQuestion = async () => {
    const question = aiQuestion.trim();
    if (!question) return;

    // Capture orchestrator mode synchronously — it resets in finally so the
    // button stays highlighted during the (potentially long) investigation.
    const isInvestigating = useOrchestrator;

    // ── Generate / reuse conversation ID SYNCHRONOUSLY before any await ───────
    // This is the only safe pattern: a local variable captured by this closure
    // is immune to React re-renders and component remounts that would reset a ref.
    let convId = activeConversationRef.current;
    const isNewConversation = !convId;
    if (isNewConversation) {
      convId = crypto.randomUUID();
      // Write ref immediately — next call sees it even if this await hasn't resolved yet
      activeConversationRef.current = convId;
      setActiveConversationId(convId);
    }

    // Append user message immediately for responsive feel
    setChatMessages(prev => [...prev, { role: 'user' as const, content: question }]);
    setAiQuestion('');
    // Reset textarea height back to one line after clearing
    if (questionInputRef.current) {
      questionInputRef.current.style.height = 'auto';
      questionInputRef.current.focus();
    }
    setAiLoading(true);
    setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);

    try {
      const token = await getAccessToken();
      const headers = new Headers({
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      });
      if (impersonatedUser && typeof impersonatedUser === 'string')
        headers.set('X-Impersonate-User', impersonatedUser);

      // Pick endpoint: orchestrator for deep investigation, standard agent otherwise
      const endpoint = isInvestigating
        ? '/api/admin/analytics/investigate'
        : '/api/admin/analytics/ask';

      const res = await fetch(`${API_BASE_URL}${endpoint}`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ question, conversation_id: convId })
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();

      // Refresh sidebar once after the first message in a new conversation
      if (isNewConversation) {
        fetchConversations();
      }

      setChatMessages(prev => [...prev, {
        role: 'assistant' as const,
        content: data.answer,
        ...(data.export ? { export: data.export } : {}),
      }]);
      setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    } catch (err) {
      setChatMessages(prev => [...prev, {
        role: 'assistant' as const,
        content: `Error: ${err instanceof Error ? err.message : 'Failed to get a response.'}`,
      }]);
    } finally {
      setAiLoading(false);
      // One-shot: reset investigate mode after each submission
      if (isInvestigating) setUseOrchestrator(false);
    }
  };

  if (error && selectedTab !== 'ask') {
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
      <div className="flex gap-2 border-b border-gray-200 overflow-x-auto">
        {[
          { id: 'ask', label: t('analytics.tabs.ask'), icon: Send },
          { id: 'overview', label: t('analytics.tabs.overview'), icon: BarChart3 },
          { id: 'spending', label: t('analytics.tabs.spending'), icon: TrendingUp },
          { id: 'forecast', label: t('analytics.tabs.forecast'), icon: LineChart },
          { id: 'fraud', label: t('analytics.tabs.fraud'), icon: AlertTriangle },
          { id: 'diversity', label: t('analytics.tabs.diversity'), icon: Users },
          { id: 'integrity', label: t('analytics.tabs.integrity'), icon: ShieldAlert }
        ].map(tab => {
          const TabIcon = tab.icon;
          const tabId = tab.id as AnalyticsTab;
          const isActive = selectedTab === tabId;
          return (
            <button
              key={tab.id}
              onClick={() => handleTabChange(tabId)}
              className={`flex items-center gap-2 px-4 py-3 font-medium transition-colors whitespace-nowrap ${
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

      {/* Loading Spinner for data tabs */}
      {loading && selectedTab !== 'ask' && (
        <div className="flex items-center justify-center h-96">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
        </div>
      )}

      {/* Overview Tab */}
      {selectedTab === 'overview' && !loading && overview && (
        <div className="space-y-6">
          {/* Key Metrics Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard
              icon={DollarSign}
              label="Total Spent"
              value={formatCurrency(overview.totalAmountSpent)}
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
              change={`Avg award: ${formatCurrency(Math.round(overview.averageAwardAmount))}`}
            />
            <MetricCard
              icon={AlertTriangle}
              label="Fraud Alerts"
              value={overview.fraudAlertsThisMonth.toString()}
              change={`Rejection rate: ${(overview.rejectionRate * 100).toFixed(1)}%`}
              warning={overview.fraudAlertsThisMonth > 0}
            />
          </div>

          {/* Category Breakdown — only shown when tenant has categories */}
          {categoryBreakdown.length > 0 && (
            <div className="bg-white rounded-lg border border-gray-200 p-6">
              <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
                <BarChart3 size={20} />
                Nominations by Category
              </h2>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 text-left text-gray-500 text-xs uppercase tracking-wide">
                    <th className="pb-2 font-medium">Category</th>
                    <th className="pb-2 font-medium text-right">Nominations</th>
                    <th className="pb-2 font-medium text-right">Total Spend</th>
                    <th className="pb-2 font-medium text-right">Avg Award</th>
                  </tr>
                </thead>
                <tbody>
                  {categoryBreakdown.map((cat, i) => {
                    const maxCount = categoryBreakdown[0]?.nominationCount || 1;
                    const barWidth = Math.round((cat.nominationCount / maxCount) * 100);
                    return (
                      <tr key={i} className="border-b border-gray-100 last:border-0">
                        <td className="py-3 pr-4">
                          <div className="flex flex-col gap-1">
                            <span className="font-medium text-gray-800">{cat.categoryDescription}</span>
                            <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden w-48">
                              <div
                                className="h-full rounded-full"
                                style={{ width: `${barWidth}%`, backgroundColor: 'var(--color-primary)' }}
                              />
                            </div>
                          </div>
                        </td>
                        <td className="py-3 text-right font-semibold text-gray-700">{cat.nominationCount}</td>
                        <td className="py-3 text-right text-gray-600">{formatCurrency(cat.totalAmount)}</td>
                        <td className="py-3 text-right text-gray-600">{formatCurrency(cat.avgAmount)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

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
      {selectedTab === 'spending' && !loading && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">30-Day Spending Trends</h2>
          <SpendingTrendChart trends={trends} formatCurrency={formatCurrency} />
        </div>
      )}

      {/* Forecasting Tab */}
      {selectedTab === 'forecast' && (
        <div className="space-y-6">
          {/* Full spinner only on the initial load; on a re-fetch (Project /
              Refresh) we keep the existing cards mounted so the page doesn't
              collapse to a spinner and jump to the top. */}
          {forecastLoading && !forecast && (
            <div className="flex justify-center py-12">
              <RefreshCw className="animate-spin text-blue-600" size={28} />
            </div>
          )}

          {forecast && (
            <>
              {/* Model comparison (bake-off) */}
              {forecast.modelComparison && (
                <div className="bg-white rounded-lg border border-gray-200 p-6">
                  <div className="flex items-start justify-between gap-4">
                    <h3 className="text-base font-semibold mb-1">Model comparison (backtest)</h3>
                    <button
                      type="button"
                      onClick={() => fetchForecast(appliedBudget)}
                      disabled={forecastLoading}
                      className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors shrink-0 disabled:opacity-50"
                    >
                      <RefreshCw size={14} className={forecastLoading ? 'animate-spin' : ''} /> Refresh
                    </button>
                  </div>
                  <p className="text-xs text-gray-500 mb-4">
                    Rolling-origin backtest error per model; lowest MASE wins (★). Seasonal-Naive
                    and ETS run weekly; LightGBM uses lag + calendar features.
                  </p>
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {(['nominations_total', 'spend_total'] as const).map(seriesKey => {
                      const block = forecast.modelComparison?.[seriesKey];
                      if (!block) return null;
                      const chosen = block['chosen'] as string;
                      const models = Object.keys(block).filter(k => k !== 'chosen');
                      return (
                        <div key={seriesKey}>
                          <p className="text-sm font-medium mb-2">
                            {seriesKey === 'nominations_total' ? 'Nominations / week' : 'Spend / week'}
                          </p>
                          <table className="w-full text-sm">
                            <thead>
                              <tr className="text-left text-gray-500 border-b">
                                <th className="py-1.5 pr-3">Model</th>
                                <th className="py-1.5 pr-3">MASE</th>
                                <th className="py-1.5 pr-3">sMAPE</th>
                                <th className="py-1.5">Coverage</th>
                              </tr>
                            </thead>
                            <tbody>
                              {models.map(mName => {
                                const mm = block[mName] as ModelMetric;
                                const isBest = mName === chosen;
                                return (
                                  <tr key={mName} className={`border-b border-gray-100 ${isBest ? 'font-semibold text-green-700' : ''}`}>
                                    <td className="py-1.5 pr-3">{mName}{isBest ? ' ★' : ''}</td>
                                    <td className="py-1.5 pr-3">{mm?.MASE != null ? mm.MASE.toFixed(3) : '—'}</td>
                                    <td className="py-1.5 pr-3">{mm?.sMAPE != null ? mm.sMAPE.toFixed(1) : '—'}</td>
                                    <td className="py-1.5">{mm?.coverage != null ? `${(mm.coverage * 100).toFixed(0)}%` : '—'}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Department forecasts */}
              {forecast.forecasts?.departments && forecast.forecasts.departments.length > 0 && (
                <div className="bg-white rounded-lg border border-gray-200 p-6">
                  <h3 className="text-base font-semibold mb-1">Department forecast (next {forecast.horizonWeeks} weeks)</h3>
                  <p className="text-xs text-gray-500 mb-4">
                    Per-department nominations and spend, each projected by the model that backtests
                    best for it (global LightGBM pools across departments; dense ones may pick ETS).
                  </p>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-gray-500 border-b">
                          <th className="py-2 pr-4">Department</th>
                          <th className="py-2 pr-4">Noms (next {forecast.horizonWeeks}w)</th>
                          <th className="py-2 pr-4">Noms range</th>
                          <th className="py-2 pr-4">Spend (next {forecast.horizonWeeks}w)</th>
                          <th className="py-2">Spend range</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...forecast.forecasts.departments]
                          .map(d => ({
                            d,
                            nSum: d.nominations.reduce((a, p) => a + p.point, 0),
                            nLo: d.nominations.reduce((a, p) => a + p.lower, 0),
                            nUp: d.nominations.reduce((a, p) => a + p.upper, 0),
                            sSum: d.spend.reduce((a, p) => a + p.point, 0),
                            sLo: d.spend.reduce((a, p) => a + p.lower, 0),
                            sUp: d.spend.reduce((a, p) => a + p.upper, 0),
                          }))
                          // 'Other' always last; real departments by projected nominations desc
                          .sort((a, b) =>
                            a.d.title === 'Other' ? 1 : b.d.title === 'Other' ? -1 : b.nSum - a.nSum)
                          .map(({ d, nSum, nLo, nUp, sSum, sLo, sUp }) => (
                            <tr key={d.title} className="border-b border-gray-100">
                              <td className="py-2 pr-4 font-medium">
                                {d.title}
                                <span className="block text-[11px] text-gray-400">
                                  noms: {d.nominationsModel || '—'} · spend: {d.spendModel || '—'}
                                </span>
                              </td>
                              <td className="py-2 pr-4 font-semibold">{nSum.toFixed(0)}</td>
                              <td className="py-2 pr-4 text-gray-500">{nLo.toFixed(0)} – {nUp.toFixed(0)}</td>
                              <td className="py-2 pr-4 font-semibold">{formatCurrency(Math.round(sSum))}</td>
                              <td className="py-2 text-gray-500">{formatCurrency(Math.round(sLo))} – {formatCurrency(Math.round(sUp))}</td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Review-load chart */}
              <div className="bg-white rounded-lg border border-gray-200 p-6">
                <h3 className="text-base font-semibold mb-4">Projected nominations &amp; reviews per week</h3>
                <ForecastBandChart
                  history={forecast.reviewLoad.history.map(h => ({ x: h.weekStart, y: h.nominations }))}
                  forecast={forecast.reviewLoad.forecast.map(f => ({
                    x: f.weekStart, y: f.projectedNominations,
                    lo: f.projectedNominationsLower, up: f.projectedNominationsUpper,
                  }))}
                  yLabel="Nominations / week"
                  color="#2563eb"
                  forecastColor="#7c3aed"
                />
              </div>

              {/* Spend history → forecast chart */}
              {forecast.forecasts?.spendWeekly && forecast.forecasts.spendWeekly.length > 0 && (
                <div className="bg-white rounded-lg border border-gray-200 p-6">
                  <h3 className="text-base font-semibold mb-4">Projected award spend per week</h3>
                  <ForecastBandChart
                    history={(forecast.forecasts.spendHistory ?? []).map(h => ({ x: h.weekStart, y: h.amount }))}
                    forecast={forecast.forecasts.spendWeekly.map(f => ({
                      x: f.weekStart ?? '', y: f.point, lo: f.lower, up: f.upper,
                    }))}
                    yLabel="Spend / week"
                    color="#16a34a"
                    forecastColor="#15803d"
                    valueFormat={(n) => formatCurrency(Math.round(n))}
                  />
                </div>
              )}

              {/* HRBP Review-Load header: assumptions + model note. Sits here
                  because its review-rate / SLA tiles feed the queue-depth calc below. */}
              <div className="bg-white rounded-lg border border-gray-200 p-6">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div>
                    <h2 className="text-lg font-semibold flex items-center gap-2">
                      <LineChart size={20} className="text-blue-600" />
                      HRBP Review-Load Forecast
                    </h2>
                    <p className="text-sm text-gray-600 mt-1">
                      Projected HRBP reviews per week for the next {forecast.horizonWeeks} weeks,
                      with {Math.round(forecast.confidence * 100)}% prediction intervals.
                    </p>
                    <span className={`inline-block mt-2 text-[11px] px-2 py-0.5 rounded-full font-medium ${
                      forecast.source === 'stored_run' ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
                    }`}>
                      {forecast.source === 'stored_run'
                        ? `weekly model run · ${forecast.runId?.slice(0, 8)}`
                        : 'live fallback (Holt) · weekly run pending'}
                    </span>
                  </div>
                </div>

                {/* Inputs / assumptions */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                  <div className="bg-gray-50 rounded-lg p-3">
                    <p className="text-xs text-gray-500">Flag / review rate</p>
                    <p className="text-xl font-bold">{(forecast.inputs.reviewRate * 100).toFixed(1)}%</p>
                    <p className="text-[11px] text-gray-400">
                      {forecast.inputs.reviewRateIsDefault
                        ? 'default (no history)'
                        : `${forecast.inputs.flaggedNominations}/${forecast.inputs.totalNominationsWindow} nominations`}
                    </p>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3">
                    <p className="text-xs text-gray-500">Avg days to approval (SLA)</p>
                    <p className="text-xl font-bold">{forecast.inputs.avgDaysToApproval.toFixed(1)}</p>
                    <p className="text-[11px] text-gray-400">used for queue depth</p>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3">
                    <p className="text-xs text-gray-500">History learned from</p>
                    <p className="text-xl font-bold">{forecast.inputs.weeklyObservations} wks</p>
                    <p className="text-[11px] text-gray-400">{forecast.historyDays}-day window</p>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3">
                    <p className="text-xs text-gray-500">Chosen model</p>
                    <p className="text-xl font-bold">
                      {forecast.forecasts?.nominationsWeekly?.[0]?.model || 'Holt linear'}
                    </p>
                    <p className="text-[11px] text-gray-400">
                      {forecast.source === 'stored_run' ? 'selected by backtest MASE' : 'live fallback'}
                    </p>
                  </div>
                </div>

                <div className="mt-4 flex items-start gap-2 text-xs text-gray-500 bg-blue-50 border border-blue-100 rounded-lg p-3">
                  <AlertCircle size={14} className="text-blue-500 mt-0.5 shrink-0" />
                  <span>{forecast.inputs.note}</span>
                </div>
              </div>

              {/* Queue-depth table */}
              <div className="bg-white rounded-lg border border-gray-200 p-6">
                <h3 className="text-base font-semibold mb-1">Expected HRBP queue depth</h3>
                <p className="text-xs text-gray-500 mb-4">
                  Reviews/week translated to concurrent queue depth via Little&apos;s Law
                  (L = arrival rate &times; {forecast.inputs.avgDaysToApproval.toFixed(1)}-day time-in-queue).
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-gray-500 border-b">
                        <th className="py-2 pr-4">Week of</th>
                        <th className="py-2 pr-4">Proj. reviews</th>
                        <th className="py-2 pr-4">Reviews range</th>
                        <th className="py-2 pr-4">Queue depth</th>
                        <th className="py-2">Queue range</th>
                      </tr>
                    </thead>
                    <tbody>
                      {forecast.reviewLoad.forecast.map(f => (
                        <tr key={f.weekIndex} className="border-b border-gray-100">
                          <td className="py-2 pr-4 font-medium">{f.weekStart}</td>
                          <td className="py-2 pr-4">{f.projectedReviews.toFixed(1)}</td>
                          <td className="py-2 pr-4 text-gray-500">{f.projectedReviewsLower.toFixed(1)} – {f.projectedReviewsUpper.toFixed(1)}</td>
                          <td className="py-2 pr-4 font-semibold">{f.projectedQueueDepth.toFixed(1)}</td>
                          <td className="py-2 text-gray-500">{f.projectedQueueDepthLower.toFixed(1)} – {f.projectedQueueDepthUpper.toFixed(1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Budget pacing */}
              <div className="bg-white rounded-lg border border-gray-200 p-6">
                <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
                  <h3 className="text-base font-semibold flex items-center gap-2">
                    <DollarSign size={18} className="text-green-600" />
                    Recognition-budget pacing
                  </h3>
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-gray-500">Annual budget</span>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={budgetInput}
                      onChange={e => setBudgetInput(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') applyBudget(); }}
                      placeholder="e.g. 500000"
                      className="w-36 px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      type="button"
                      onClick={applyBudget}
                      disabled={forecastLoading}
                      className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {forecastLoading ? 'Projecting…' : 'Project'}
                    </button>
                  </div>
                </div>

                {!forecast.budgetPacing && (
                  <p className="text-sm text-gray-500 py-6 text-center">
                    Enter your annual recognition budget above to project cumulative spend and the expected exhaustion date.
                  </p>
                )}

                {forecast.budgetPacing && (
                  <>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-5">
                      <div className="bg-gray-50 rounded-lg p-3">
                        <p className="text-xs text-gray-500">Spent to date (FY)</p>
                        <p className="text-xl font-bold">{formatCurrency(forecast.budgetPacing.spentToDate)}</p>
                      </div>
                      <div className="bg-gray-50 rounded-lg p-3">
                        <p className="text-xs text-gray-500">Annual budget</p>
                        <p className="text-xl font-bold">{formatCurrency(forecast.budgetPacing.annualBudget)}</p>
                      </div>
                      <div className="bg-gray-50 rounded-lg p-3">
                        <p className="text-xs text-gray-500">Projected exhaustion</p>
                        <p className={`text-xl font-bold ${forecast.budgetPacing.exhaustionDate ? 'text-orange-600' : 'text-green-600'}`}>
                          {forecast.budgetPacing.exhaustionDate || 'Within budget'}
                        </p>
                        {forecast.budgetPacing.exhaustionDate && (
                          <p className="text-[11px] text-gray-400">
                            range {forecast.budgetPacing.exhaustionDateEarliest || '—'} to {forecast.budgetPacing.exhaustionDateLatest || '—'}
                          </p>
                        )}
                      </div>
                      <div className="bg-gray-50 rounded-lg p-3">
                        <p className="text-xs text-gray-500">Utilization @ +{forecast.horizonWeeks}w</p>
                        <p className="text-xl font-bold">
                          {forecast.budgetPacing.budgetUtilizationAtHorizon != null
                            ? `${(forecast.budgetPacing.budgetUtilizationAtHorizon * 100).toFixed(0)}%`
                            : '—'}
                        </p>
                      </div>
                    </div>
                    <BudgetPacingChart
                      points={forecast.budgetPacing.cumulative}
                      budget={forecast.budgetPacing.annualBudget}
                      formatCurrency={formatCurrency}
                    />
                  </>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* Fraud Alerts Tab */}
      {selectedTab === 'fraud' && !loading && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            <span className="text-red-600">
              <AlertTriangle size={20} />
            </span>
            Recent Fraud Alerts
          </h2>
          <FraudAlertsList alerts={fraudAlerts} onOpenNominationLogs={onOpenNominationLogs} />
        </div>
      )}

      {/* Diversity Metrics Tab */}
      {selectedTab === 'diversity' && !loading && diversityMetrics && (
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

      {/* Ask Analytics Tab — sidebar + chat */}
      {selectedTab === 'ask' && (
        <div className="flex gap-0 rounded-lg border border-gray-200 overflow-hidden bg-white" style={{ height: 'calc(100vh - 160px)' }}>

          {/* ── Conversation sidebar ── */}
          <div className="w-64 shrink-0 border-r border-gray-100 flex flex-col bg-gray-50">
            <div className="px-3 py-3 border-b border-gray-100 flex gap-2">
              <button
                onClick={startNewConversation}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
              >
                <Send size={14} />
                New conversation
              </button>
              <button
                onClick={fetchConversations}
                disabled={convLoading}
                className="p-2 text-gray-400 hover:text-gray-700 hover:bg-gray-200 rounded-lg transition-colors disabled:opacity-40"
                title="Refresh conversation list"
              >
                <RefreshCw size={14} className={convLoading ? 'animate-spin' : ''} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto py-2">
              {convLoading && (
                <p className="text-xs text-gray-400 text-center py-4">Loading…</p>
              )}
              {!convLoading && conversations.length === 0 && (
                <p className="text-xs text-gray-400 text-center py-6 px-3">No conversations yet</p>
              )}
              {conversations.map(conv => (
                <div
                  key={conv.conversationId}
                  onClick={() => editingConvId !== conv.conversationId && loadConversation(conv.conversationId)}
                  className={`group flex items-start justify-between gap-1 px-3 py-2 mx-1 rounded-lg cursor-pointer transition-colors ${
                    activeConversationId === conv.conversationId
                      ? 'bg-blue-50 border border-blue-200'
                      : 'hover:bg-gray-100'
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    {editingConvId === conv.conversationId ? (
                      <input
                        autoFocus
                        value={editingTitle}
                        onChange={e => setEditingTitle(e.target.value)}
                        onBlur={() => renameConversation(conv.conversationId, editingTitle)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') renameConversation(conv.conversationId, editingTitle);
                          if (e.key === 'Escape') setEditingConvId(null);
                        }}
                        onClick={e => e.stopPropagation()}
                        className="w-full text-xs font-medium text-gray-800 bg-white border border-blue-400 rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    ) : (
                      <p
                        className="text-xs font-medium text-gray-800 truncate"
                        onDoubleClick={e => {
                          e.stopPropagation();
                          setEditingConvId(conv.conversationId);
                          setEditingTitle(conv.title);
                        }}
                        title="Double-click to rename"
                      >
                        {conv.title}
                      </p>
                    )}
                    <p className="text-xs text-gray-400 mt-0.5">
                      {new Date(conv.updatedAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                    </p>
                  </div>
                  <button
                    onClick={(e) => deleteConversation(conv.conversationId, e)}
                    className="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-400 transition-all shrink-0 mt-0.5"
                    title="Delete conversation"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* ── Chat panel ── */}
          <div className="flex-1 flex flex-col min-w-0">

            {/* Header */}
            <div className="px-6 py-4 border-b border-gray-100 shrink-0">
              <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
                <Send size={16} />
                {activeConversationId
                  ? (conversations.find(c => c.conversationId === activeConversationId)?.title ?? 'Conversation')
                  : 'Ask Analytics AI'}
              </h2>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
              {chatMessages.length === 0 && (
                <div className="flex flex-col items-center justify-center h-full text-center">
                  <Send size={40} className="text-gray-200 mb-4" />
                  <p className="text-gray-500 font-medium mb-1">Ask anything about your nominations</p>
                  <p className="text-sm text-gray-400">Trends, fraud patterns, graph relationships, exports — all in one conversation.</p>
                </div>
              )}

              {chatMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-3xl rounded-2xl px-4 py-3 ${
                    msg.role === 'user'
                      ? 'bg-blue-600 text-white rounded-br-sm'
                      : 'bg-gray-100 text-gray-800 rounded-bl-sm'
                  }`}>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                    {msg.export && (
                      <a
                        href={msg.export.download_url}
                        download={msg.export.filename}
                        className="inline-flex items-center gap-2 mt-3 px-3 py-1.5 bg-white text-blue-600 text-xs font-medium rounded-lg hover:bg-blue-50 transition-colors border border-blue-200"
                      >
                        {msg.export.label}
                      </a>
                    )}
                  </div>
                </div>
              ))}

              {aiLoading && (
                <div className="flex justify-start">
                  <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-3">
                    <div className="flex gap-1 items-center h-4">
                      <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                      <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                      <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                  </div>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Input bar */}
            <div className="px-6 py-4 border-t border-gray-100 shrink-0">
              <div className="flex gap-2 items-end">
                <textarea
                  ref={questionInputRef}
                  value={aiQuestion}
                  onChange={(e) => setAiQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      if (!aiLoading) handleAskQuestion();
                    }
                  }}
                  placeholder="Ask a follow-up or a new question… (Shift+Enter for new line)"
                  rows={1}
                  className="flex-1 px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-sm resize-none overflow-hidden leading-relaxed"
                  style={{ maxHeight: '160px', overflowY: 'auto' }}
                  disabled={aiLoading}
                />
                <button
                  onClick={handleAskQuestion}
                  disabled={aiLoading || !aiQuestion.trim()}
                  className={`px-5 py-3 text-white rounded-xl font-medium transition-colors disabled:bg-gray-300 flex items-center gap-2 text-sm ${
                    useOrchestrator ? 'bg-purple-600 hover:bg-purple-700' : 'bg-blue-600 hover:bg-blue-700'
                  }`}
                >
                  <Send size={16} />
                  {aiLoading ? (useOrchestrator ? 'Investigating…' : 'Thinking…') : 'Send'}
                </button>
              </div>
              {/* Footer row: Investigate toggle + hint */}
              <div className="flex items-center justify-between mt-2">
                <button
                  onClick={() => setUseOrchestrator(prev => !prev)}
                  disabled={aiLoading}
                  title="Run a deep multi-agent investigation (one-shot — resets after submit)"
                  className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-medium transition-colors disabled:opacity-40 ${
                    useOrchestrator
                      ? 'bg-purple-100 text-purple-700 border border-purple-300 hover:bg-purple-200'
                      : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100 border border-transparent'
                  }`}
                >
                  <ShieldAlert size={13} />
                  {useOrchestrator ? 'Investigate: ON' : 'Investigate'}
                </button>
                <p className="text-xs text-gray-400">Conversations saved automatically</p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Integrity Tab ──────────────────────────────────────────── */}
      {selectedTab === 'integrity' && (
        <div className="space-y-6">

          {/* Header row: title + run selector */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold text-gray-900">Graph Pattern Findings</h3>
              <p className="text-sm text-gray-500">Behavioural fraud patterns detected by the weekly analytics job</p>
            </div>
            {integrityRuns.length > 0 && (
              <div className="relative">
                <select
                  value={selectedRunId ?? ''}
                  onChange={e => handleRunChange(e.target.value)}
                  className="appearance-none pl-3 pr-10 py-2 border border-gray-300 rounded-lg text-sm bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {integrityRuns.map(run => (
                    <option key={run.runId} value={run.runId}>
                      {new Date(run.runDate).toLocaleDateString('en-US', {
                        month: 'short', day: 'numeric', year: 'numeric'
                      })} — {run.totalFindings} finding{run.totalFindings !== 1 ? 's' : ''}
                    </option>
                  ))}
                </select>
                <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
              </div>
            )}
          </div>

          {/* Loading / empty states */}
          {integrityLoading && (
            <div className="flex items-center justify-center py-16 text-gray-400">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mr-3" />
              Loading findings…
            </div>
          )}

          {!integrityLoading && integrityRuns.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <ShieldAlert size={48} className="text-gray-300 mb-4" />
              <p className="text-gray-500 font-medium">No runs yet</p>
              <p className="text-sm text-gray-400 mt-1">
                Findings will appear here after the fraud analytics job runs for the first time.
              </p>
            </div>
          )}

          {!integrityLoading && integrityRuns.length > 0 && (
            <>
              {/* Severity filter tiles */}
              {(() => {
                const counts: Record<string, number> = { Critical: 0, High: 0, Medium: 0, Low: 0 };
                integrityFindings.forEach(f => { counts[f.severity] = (counts[f.severity] ?? 0) + 1; });
                const hasSevFilters = activeSeverityFilters.size > 0;
                return (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {(['Critical', 'High', 'Medium', 'Low'] as const).map(sev => {
                      const active = activeSeverityFilters.has(sev);
                      return (
                        <button
                          key={sev}
                          onClick={() => {
                            setActiveSeverityFilters(prev => {
                              const next = new Set(prev);
                              if (next.has(sev)) next.delete(sev); else next.add(sev);
                              return next;
                            });
                            setExpandedFinding(null);
                          }}
                          className={`p-4 rounded-lg border-2 text-center transition-all ${
                            active
                              ? `${SEVERITY_STYLES[sev].card} ring-2 ring-inset ring-gray-600`
                              : hasSevFilters
                                ? 'bg-gray-50 border-gray-200 opacity-40'
                                : `${SEVERITY_STYLES[sev].card} hover:brightness-95`
                          }`}
                        >
                          <p className="text-2xl font-bold">{counts[sev]}</p>
                          <span className={`mt-1 inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${
                            active || !hasSevFilters ? SEVERITY_STYLES[sev].badge : 'bg-gray-200 text-gray-400'
                          }`}>
                            {sev}
                          </span>
                          {active && (
                            <p className="text-xs text-gray-500 mt-1">✓ filtered</p>
                          )}
                        </button>
                      );
                    })}
                  </div>
                );
              })()}

              {/* Severity clear button */}
              {activeSeverityFilters.size > 0 && (
                <div className="flex justify-end -mt-1">
                  <button
                    onClick={() => { setActiveSeverityFilters(new Set()); setExpandedFinding(null); }}
                    className="text-sm text-gray-400 hover:text-gray-600 underline underline-offset-2"
                  >
                    Clear severity filter
                  </button>
                </div>
              )}

              {/* Pattern type filter chips */}
              {(() => {
                const byType: Record<string, number> = {};
                integrityFindings.forEach(f => { byType[f.patternType] = (byType[f.patternType] ?? 0) + 1; });
                const hasFilters = activePatternFilters.size > 0;
                return Object.keys(byType).length > 0 ? (
                  <div className="flex flex-wrap gap-2 items-center">
                    {Object.entries(byType).map(([type, count]) => {
                      const active = activePatternFilters.has(type);
                      return (
                        <button
                          key={type}
                          onClick={() => {
                            setActivePatternFilters(prev => {
                              const next = new Set(prev);
                              if (next.has(type)) next.delete(type); else next.add(type);
                              return next;
                            });
                            setExpandedFinding(null);
                          }}
                          className={`px-3 py-1 rounded-full text-sm font-medium transition-colors ${
                            active
                              ? 'bg-gray-700 text-white'
                              : hasFilters
                                ? 'bg-gray-100 text-gray-400 hover:bg-gray-200 hover:text-gray-600'
                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                          }`}
                        >
                          {PATTERN_META[type]?.label ?? type}
                          <span className={`ml-1.5 px-1.5 py-0.5 rounded-full text-xs ${
                            active ? 'bg-gray-500 text-white' : 'bg-gray-300 text-gray-700'
                          }`}>{count}</span>
                        </button>
                      );
                    })}
                    {hasFilters && (
                      <button
                        onClick={() => { setActivePatternFilters(new Set()); setExpandedFinding(null); }}
                        className="px-3 py-1 rounded-full text-sm text-gray-400 hover:text-gray-600 underline underline-offset-2"
                      >
                        Clear
                      </button>
                    )}
                  </div>
                ) : null;
              })()}

              {/* Findings list */}
              {(() => {
                const visibleFindings = integrityFindings.filter(f => {
                  const patternOk  = activePatternFilters.size === 0  || activePatternFilters.has(f.patternType);
                  const severityOk = activeSeverityFilters.size === 0 || activeSeverityFilters.has(f.severity);
                  return patternOk && severityOk;
                });
                return visibleFindings.length === 0 ? (
                  <div className="text-center py-10 text-gray-400">
                    {integrityFindings.length === 0 ? 'No findings for this run.' : 'No findings match the selected filters.'}
                  </div>
                ) : (
                <div className="space-y-3">
                  {visibleFindings.map(finding => {
                    const styles = SEVERITY_STYLES[finding.severity] ?? SEVERITY_STYLES.Low;
                    const meta   = PATTERN_META[finding.patternType];
                    const users  = (() => { try { return JSON.parse(finding.affectedUsers ?? '[]') as number[]; } catch { return []; } })();
                    const nomIds = (() => { try { return JSON.parse(finding.nominationIds ?? '[]') as number[]; } catch { return []; } })();
                    const isOpen = expandedFinding === finding.findingId;

                    return (
                      <div key={finding.findingId} className={`rounded-lg border-2 ${styles.card}`}>
                        {/* Finding header — always visible */}
                        <button
                          className="w-full text-left p-4"
                          onClick={() => setExpandedFinding(isOpen ? null : finding.findingId)}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex items-center gap-3 flex-1 min-w-0">
                              <span className={`shrink-0 px-2.5 py-1 rounded-full text-xs font-semibold ${styles.badge}`}>
                                {finding.severity}
                              </span>
                              <span className="font-semibold text-gray-900 truncate">
                                {meta?.label ?? finding.patternType}
                              </span>
                              {finding.findingScore != null && (
                                <span className="shrink-0 rounded bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700">
                                  Score {finding.findingScore.toFixed(2)}
                                </span>
                              )}
                              <span className="shrink-0 font-mono text-xs text-gray-400 bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded">
                                #{finding.findingId}
                              </span>
                              {meta && (
                                <span className="hidden sm:block text-xs text-gray-500 truncate">
                                  {meta.description}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-3 shrink-0">
                              {finding.totalAmount != null && finding.totalAmount > 0 && (
                                <span className="text-xs font-semibold text-gray-700 bg-gray-100 px-2.5 py-1 rounded-full">
                                  {formatCurrency(finding.totalAmount)}
                                </span>
                              )}
                              <ChevronDown
                                size={16}
                                className={`text-gray-400 transition-transform mt-0.5 ${isOpen ? 'rotate-180' : ''}`}
                              />
                              {/* Export button — sits inside the accordion button but
                                  stopPropagation prevents the toggle from firing */}
                              <span
                                role="button"
                                title="Export to Excel"
                                onClick={(e) => exportFinding(finding.findingId, e)}
                                className="p-1 rounded hover:bg-white/60 text-gray-400 hover:text-green-700 transition-colors"
                              >
                                <Download size={15} />
                              </span>
                            </div>
                          </div>
                          <p className="mt-2 text-sm text-gray-700 line-clamp-2">{finding.detail}</p>
                        </button>

                        {/* Expanded detail */}
                        {isOpen && (
                          <div className="px-4 pb-4 space-y-3 border-t border-current border-opacity-20 pt-3">
                            {finding.totalAmount != null && finding.totalAmount > 0 && (
                              <div>
                                <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-1">
                                  Total Approved / Paid
                                </p>
                                <p className="text-sm font-bold text-gray-900">
                                  {formatCurrency(finding.totalAmount)}
                                </p>
                              </div>
                            )}
                            {users.length > 0 && (
                              <div>
                                <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-1">
                                  Affected Users
                                </p>
                                <div className="flex flex-wrap gap-1.5">
                                  {users.map(uid => (
                                    <span key={uid} className="px-2 py-0.5 bg-white rounded border border-gray-300 text-xs font-mono text-gray-700">
                                      #{uid}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {nomIds.length > 0 && (
                              <div>
                                <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-1">
                                  Nominations
                                </p>
                                <div className="flex flex-wrap gap-1.5">
                                  {nomIds.map(nid => (
                                    <span key={nid} className="px-2 py-0.5 bg-white rounded border border-gray-300 text-xs font-mono text-gray-700">
                                      #{nid}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            <p className="text-xs text-gray-400">
                              Detected {new Date(finding.detectedAt).toLocaleString()}
                            </p>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                );
              })()}
            </>
          )}
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
      <span className={positive ? 'text-green-600' : warning ? 'text-red-600' : 'text-blue-600'}>
        <Icon size={24} />
      </span>
    </div>
  </div>
);

interface DepartmentTableProps {
  departments: DepartmentSpending[];
}

const DepartmentTable: React.FC<DepartmentTableProps> = ({ departments }) => {
  const { formatCurrency } = useTenantConfig();
  return (
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
            <td className="text-right px-4 py-3 font-semibold">{formatCurrency(dept.totalSpent)}</td>
            <td className="text-right px-4 py-3">{formatCurrency(Math.round(dept.averageAmount))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
  );
};

interface RecipientListProps {
  recipients: TopRecipient[];
}

const RecipientList: React.FC<RecipientListProps> = ({ recipients }) => {
  const { formatCurrency } = useTenantConfig();
  return (
  <div className="space-y-3">
    {recipients.map((person, i) => (
      <div key={i} className="flex items-center justify-between p-3 bg-gray-50 rounded">
        <div>
          <p className="font-medium">{person.FirstName} {person.LastName}</p>
          <p className="text-xs text-gray-600">{person.nominationCount} awards</p>
        </div>
        <p className="font-semibold">{formatCurrency(person.totalAmount)}</p>
      </div>
    ))}
  </div>
  );
};

interface SpendingTrendChartProps {
  trends: SpendingTrend[];
  formatCurrency: (n: number) => string;
}

const SpendingTrendChart: React.FC<SpendingTrendChartProps> = ({ trends, formatCurrency }) => {
  // API returns newest-first; reverse to oldest→newest, then take the most
  // recent 30 (slice(-30)) so the last bar is the latest day, not the oldest.
  const sorted = [...trends].reverse();
  const shown = sorted.slice(-30);
  const maxAmount = Math.max(...shown.map(t => t.amount), 1);

  return (
    <div className="space-y-4">
      <div className="h-64 flex items-end gap-1 border-l border-b border-gray-300 p-4">
        {shown.map((trend, i) => (
          <div
            key={i}
            className="flex-1 bg-blue-500 rounded-t hover:bg-blue-600 transition-colors relative group"
            style={{
              height: `${(trend.amount / maxAmount) * 100}%`,
              minHeight: '4px'
            }}
            title={`${trend.date}: ${formatCurrency(trend.amount)}`}
          >
            <div className="opacity-0 group-hover:opacity-100 absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 bg-gray-900 text-white text-xs px-2 py-1 rounded whitespace-nowrap">
              {formatCurrency(trend.amount)}
            </div>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-600 text-center">Last 30 days</p>
    </div>
  );
};

// ── Forecast band chart (SVG, no chart lib) ──────────────────────────────────
interface BandPoint { x: string; y: number; lo?: number; up?: number; }
interface ForecastBandChartProps {
  history: BandPoint[];
  forecast: BandPoint[];
  yLabel: string;
  color: string;
  forecastColor: string;
  valueFormat?: (n: number) => string;
}

const ForecastBandChart: React.FC<ForecastBandChartProps> = ({ history, forecast, yLabel, color, forecastColor, valueFormat }) => {
  const fmt = valueFormat ?? ((n: number) => Math.round(n).toLocaleString());
  const W = 800, H = 320, padL = 48, padR = 16, padT = 16, padB = 40;
  const all = [...history, ...forecast];
  if (all.length === 0) return <p className="text-sm text-gray-500">No data.</p>;

  const n = all.length;
  const yMax = Math.max(1, ...all.map(p => Math.max(p.y, p.up ?? 0)));
  const yMin = 0;
  const xAt = (i: number) => padL + (n === 1 ? 0 : (i / (n - 1)) * (W - padL - padR));
  const yAt = (v: number) => H - padB - ((v - yMin) / (yMax - yMin)) * (H - padT - padB);

  const histPts = history.map((p, i) => `${xAt(i)},${yAt(p.y)}`).join(' ');
  const fStart = history.length;
  // Connect last history point into the forecast line for visual continuity.
  const lastHist = history.length ? `${xAt(history.length - 1)},${yAt(history[history.length - 1].y)} ` : '';
  const fcPts = lastHist + forecast.map((p, i) => `${xAt(fStart + i)},${yAt(p.y)}`).join(' ');

  // Confidence band polygon (upper edge forward, lower edge back), anchored at last history point.
  const upper = forecast.map((p, i) => `${xAt(fStart + i)},${yAt(p.up ?? p.y)}`);
  const lower = forecast.map((p, i) => `${xAt(fStart + i)},${yAt(p.lo ?? p.y)}`).reverse();
  const anchor = history.length ? `${xAt(history.length - 1)},${yAt(history[history.length - 1].y)}` : '';
  const bandPath = [anchor, ...upper, ...lower].filter(Boolean).join(' ');

  const dividerX = history.length ? xAt(history.length - 1) : padL;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => yMin + f * (yMax - yMin));
  const labelIdx = Array.from(new Set([0, Math.floor(n / 2), n - 1])).filter(i => i >= 0 && i < n);

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 520 }} role="img" aria-label="forecast chart">
        {/* gridlines + y ticks */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} y1={yAt(t)} x2={W - padR} y2={yAt(t)} stroke="#eef2f7" strokeWidth={1} />
            <text x={padL - 6} y={yAt(t) + 4} textAnchor="end" fontSize={11} fill="#94a3b8">{fmt(t)}</text>
          </g>
        ))}
        {/* forecast region shading divider */}
        <line x1={dividerX} y1={padT} x2={dividerX} y2={H - padB} stroke="#cbd5e1" strokeWidth={1} strokeDasharray="3 3" />
        <text x={dividerX + 4} y={padT + 12} fontSize={10} fill="#94a3b8">forecast →</text>
        {/* confidence band */}
        {bandPath && <polygon points={bandPath} fill={forecastColor} opacity={0.16} />}
        {/* history line */}
        {history.length > 1 && <polyline points={histPts} fill="none" stroke={color} strokeWidth={2.5} />}
        {history.map((p, i) => <circle key={`h${i}`} cx={xAt(i)} cy={yAt(p.y)} r={3} fill={color} />)}
        {/* forecast line (dashed) */}
        <polyline points={fcPts} fill="none" stroke={forecastColor} strokeWidth={2.5} strokeDasharray="6 4" />
        {forecast.map((p, i) => <circle key={`f${i}`} cx={xAt(fStart + i)} cy={yAt(p.y)} r={3} fill={forecastColor} />)}
        {/* transparent hover targets with native tooltips (date + exact value, band for forecast) */}
        {history.map((p, i) => (
          <circle key={`ht${i}`} cx={xAt(i)} cy={yAt(p.y)} r={9} fill="transparent" style={{ cursor: 'pointer' }}>
            <title>{`${p.x}: ${fmt(p.y)}`}</title>
          </circle>
        ))}
        {forecast.map((p, i) => (
          <circle key={`ft${i}`} cx={xAt(fStart + i)} cy={yAt(p.y)} r={9} fill="transparent" style={{ cursor: 'pointer' }}>
            <title>{`${p.x}: ${fmt(p.y)}  (${fmt(p.lo ?? p.y)} – ${fmt(p.up ?? p.y)})`}</title>
          </circle>
        ))}
        {/* x labels */}
        {labelIdx.map(i => (
          <text key={`x${i}`} x={xAt(i)} y={H - padB + 16} textAnchor="middle" fontSize={10} fill="#94a3b8">
            {all[i].x.slice(5)}
          </text>
        ))}
        <text x={12} y={padT + 4} fontSize={10} fill="#94a3b8" transform={`rotate(-90 12 ${H / 2})`}>{yLabel}</text>
      </svg>
      <div className="flex gap-4 text-xs text-gray-500 mt-2 pl-12">
        <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5" style={{ background: color }} /> Observed</span>
        <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 border-t border-dashed" style={{ borderColor: forecastColor }} /> Forecast</span>
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm" style={{ background: forecastColor, opacity: 0.16 }} /> Prediction interval</span>
      </div>
    </div>
  );
};

// ── Budget pacing chart (cumulative actual vs projected vs budget line) ───────
interface BudgetPacingChartProps {
  points: BudgetCumulativePoint[];
  budget: number;
  formatCurrency: (n: number) => string;
}
const BudgetPacingChart: React.FC<BudgetPacingChartProps> = ({ points, budget, formatCurrency }) => {
  const W = 800, H = 300, padL = 64, padR = 16, padT = 16, padB = 40;
  if (!points.length) return null;
  const n = points.length;
  const yMax = Math.max(budget * 1.05, ...points.map(p => Math.max(p.actual ?? 0, p.upper ?? p.projected ?? 0)));
  const xAt = (i: number) => padL + (n === 1 ? 0 : (i / (n - 1)) * (W - padL - padR));
  const yAt = (v: number) => H - padB - (v / yMax) * (H - padT - padB);

  const actualPts = points.map((p, i) => p.actual != null ? `${xAt(i)},${yAt(p.actual)}` : null).filter(Boolean).join(' ');
  const lastActualIdx = points.reduce((acc, p, i) => p.actual != null ? i : acc, -1);
  const projStartAnchor = lastActualIdx >= 0 ? `${xAt(lastActualIdx)},${yAt(points[lastActualIdx].actual as number)} ` : '';
  const projPts = projStartAnchor + points.map((p, i) => p.projected != null ? `${xAt(i)},${yAt(p.projected)}` : null).filter(Boolean).join(' ');

  const up = points.map((p, i) => p.upper != null ? `${xAt(i)},${yAt(p.upper)}` : null).filter(Boolean);
  const lo = points.map((p, i) => p.lower != null ? `${xAt(i)},${yAt(p.lower)}` : null).filter(Boolean).reverse();
  const band = [...up, ...lo].join(' ');

  const ticks = [0, 0.5, 1].map(f => f * yMax);
  const labelIdx = Array.from(new Set([0, Math.floor(n / 2), n - 1])).filter(i => i >= 0 && i < n);

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 520 }} role="img" aria-label="budget pacing chart">
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} y1={yAt(t)} x2={W - padR} y2={yAt(t)} stroke="#eef2f7" strokeWidth={1} />
            <text x={padL - 6} y={yAt(t) + 4} textAnchor="end" fontSize={11} fill="#94a3b8">{formatCurrency(Math.round(t))}</text>
          </g>
        ))}
        {/* budget threshold */}
        <line x1={padL} y1={yAt(budget)} x2={W - padR} y2={yAt(budget)} stroke="#dc2626" strokeWidth={1.5} strokeDasharray="5 4" />
        <text x={W - padR} y={yAt(budget) - 5} textAnchor="end" fontSize={10} fill="#dc2626">Annual budget</text>
        {band && <polygon points={band} fill="#16a34a" opacity={0.14} />}
        {actualPts && <polyline points={actualPts} fill="none" stroke="#16a34a" strokeWidth={2.5} />}
        <polyline points={projPts} fill="none" stroke="#16a34a" strokeWidth={2.5} strokeDasharray="6 4" />
        {labelIdx.map(i => (
          <text key={`x${i}`} x={xAt(i)} y={H - padB + 16} textAnchor="middle" fontSize={10} fill="#94a3b8">
            {points[i].weekStart.slice(5)}
          </text>
        ))}
      </svg>
      <div className="flex gap-4 text-xs text-gray-500 mt-2 pl-16">
        <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 bg-green-600" /> Actual cumulative</span>
        <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 border-t border-dashed border-green-600" /> Projected</span>
        <span className="flex items-center gap-1"><span className="inline-block w-4 h-0.5 border-t border-dashed border-red-600" /> Budget</span>
      </div>
    </div>
  );
};

interface FraudAlertsListProps {
  alerts: FraudAlert[];
  onOpenNominationLogs: (nominationId: number) => void;
}

const FraudAlertsList: React.FC<FraudAlertsListProps> = ({ alerts, onOpenNominationLogs }) => {
  const { formatCurrency } = useTenantConfig();
  if (!alerts.length) {
    return <p className="text-center text-gray-600 py-8">No fraud alerts detected</p>;
  }

  return (
    <div className="space-y-3">
      {alerts.map((alert) => (
        <div key={alert.NominationId} className={`p-4 rounded-lg border-2 ${
          alert.riskLevel === 'High' ? 'bg-red-50 border-red-300' : 'bg-yellow-50 border-yellow-300'
        }`}>
          <div className="flex items-start justify-between mb-2">
            <div>
              <p className="font-semibold">
                {alert.nominatorName} → {alert.beneficiaryName}
              </p>
              <p className="text-sm text-gray-600">{formatCurrency(alert.amount)} on {alert.nominationDate}</p>
              <button
                type="button"
                onClick={() => onOpenNominationLogs(alert.NominationId)}
                className="mt-1 rounded font-mono text-xs text-blue-700 hover:text-blue-900 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1"
                title="View logs for this nomination"
                aria-label={`View logs for nomination ${alert.NominationId}`}
              >
                Nomination #{alert.NominationId}
              </button>
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
