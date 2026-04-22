import React, { useState, useEffect } from 'react';
import { AlertCircle, TrendingUp, Users, DollarSign, Clock, AlertTriangle, BarChart3, Send, ShieldAlert, ChevronDown, RefreshCw, Download } from 'lucide-react';
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
}

// Human-readable labels and icons per pattern type
const PATTERN_META: Record<string, { label: string; description: string }> = {
  Ring:                { label: 'Nomination Ring',        description: 'Directed cycle of mutual nominations' },
  SuperNominator:      { label: 'Super Nominator',        description: 'Unusually high nomination volume' },
  Desert:              { label: 'Nomination Desert',      description: 'Entire team absent from all nominations' },
  ApproverAffinity:    { label: 'Approver Affinity',      description: 'Elevated approval rate for specific pair' },
  CopyPaste:           { label: 'Copy-Paste Fraud',       description: 'Near-identical nomination descriptions' },
  TransactionalLanguage: { label: 'Transactional Language', description: 'Personal-benefit phrasing in description' },
  HiddenCandidate:     { label: 'Hidden Candidate',       description: 'Named in descriptions but never nominated' },
};

const SEVERITY_STYLES: Record<string, { card: string; badge: string }> = {
  Critical: { card: 'bg-red-50 border-red-300',    badge: 'bg-red-200 text-red-800' },
  High:     { card: 'bg-orange-50 border-orange-300', badge: 'bg-orange-200 text-orange-800' },
  Medium:   { card: 'bg-yellow-50 border-yellow-300', badge: 'bg-yellow-200 text-yellow-800' },
  Low:      { card: 'bg-blue-50 border-blue-300',   badge: 'bg-blue-200 text-blue-800' },
};

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<'overview' | 'spending' | 'fraud' | 'diversity' | 'ask' | 'integrity'>('ask');
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
  const questionInputRef = React.useRef<HTMLInputElement>(null);
  // Investigate mode — one-shot: resets to false after each submission
  const [useOrchestrator, setUseOrchestrator] = React.useState(false);

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
  const handleTabChange = async (tabId: string) => {
    setSelectedTab(tabId as any);
    
    // Integrity tab has its own fetch path
    if (tabId === 'integrity') {
      if (!loadedTabs.has('integrity')) {
        await fetchIntegrityRuns();
        setLoadedTabs(prev => new Set([...prev, 'integrity']));
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
    questionInputRef.current?.focus();
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
          { id: 'ask', label: 'Ask Analytics', icon: Send },
          { id: 'overview', label: 'Overview', icon: BarChart3 },
          { id: 'spending', label: 'Spending Trends', icon: TrendingUp },
          { id: 'fraud', label: 'Fraud Alerts', icon: AlertTriangle },
          { id: 'diversity', label: 'Diversity Metrics', icon: Users },
          { id: 'integrity', label: 'Integrity', icon: ShieldAlert }
        ].map(tab => {
          const TabIcon = tab.icon;
          const isActive = selectedTab === (tab.id as any);
          return (
            <button
              key={tab.id}
              onClick={() => handleTabChange(tab.id)}
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
      {selectedTab === 'spending' && !loading && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-lg font-semibold mb-4">90-Day Spending Trends</h2>
          <SpendingTrendChart trends={trends} />
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
          <FraudAlertsList alerts={fraudAlerts} />
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
              <div className="flex gap-2">
                <input
                  ref={questionInputRef}
                  type="text"
                  value={aiQuestion}
                  onChange={(e) => setAiQuestion(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && !aiLoading && handleAskQuestion()}
                  placeholder="Ask a follow-up or a new question…"
                  className="flex-1 px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-sm"
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
                                  ${finding.totalAmount.toLocaleString()}
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
                                  ${finding.totalAmount.toLocaleString()}
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
