import React, { useState } from 'react';
import {
  Activity, BrainCircuit, ChevronDown, ChevronLeft, ChevronRight, ChevronUp,
  CircleHelp, ExternalLink, FileSearch, RefreshCw, Search, Settings, ShieldAlert, Users, X,
} from 'lucide-react';
import { useImpersonation } from '../contexts/ImpersonationContext';
import {
  EngineVerdicts, RiskBadge, ShapPanel,
  type HRBPQueueItem, type PairHistory,
} from './HRBPReviewTab';
import { DetectionEnginesPanel, FraudPanel } from './SetupPanel';
import { UserAnalysisTab } from './UserAnalysisTab';

interface SearchItem {
  nomination_id: number;
  nomination_date: string;
  nominator_name: string;
  beneficiary_name: string;
  category: string | null;
  amount: number;
  currency: string;
  status: string;
  risk_level: string | null;
  composite_score: number | null;
  final_route: string | null;
  has_model_evidence: boolean;
}

interface SearchResponse {
  items: SearchItem[];
  total: number;
  page: number;
  page_size: number;
}

interface Props {
  apiFetch: <T>(path: string, options?: RequestInit, impersonatedUPN?: string) => Promise<T>;
  formatCurrency: (amount: number) => string;
  onOpenNominationLogs: (nominationId: number) => void;
}

const PAGE_SIZE = 25;
const STATUSES = ['', 'Submitted', 'Pending', 'PendingHRBPReview', 'Approved', 'Paid', 'Rejected'];
const RISKS = ['', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NONE', 'UNKNOWN'];
const todayAsLocalDate = () => {
  const today = new Date();
  const year = today.getFullYear();
  const month = String(today.getMonth() + 1).padStart(2, '0');
  const day = String(today.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const ReadOnlyEvidence: React.FC<{
  item: HRBPQueueItem;
  pairHistory: PairHistory | null;
  formatCurrency: (amount: number) => string;
}> = ({ item, pairHistory, formatCurrency }) => {
  const [showHistory, setShowHistory] = useState(false);
  return (
    <div className={`overflow-hidden rounded-lg border ${item.risk_level === 'CRITICAL' ? 'border-red-300' : 'border-gray-200'}`}>
      <div className="p-5">
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <p className="mb-0.5 text-sm text-gray-500">
              Nomination #{item.nomination_id} · {new Date(item.nomination_date).toLocaleDateString()}
            </p>
            <p className="text-lg font-semibold text-gray-900">
              {item.nominator_name} → {item.beneficiary_name}
            </p>
            <p className="text-sm text-gray-500">{item.nominator_email}</p>
            <p className="text-xs text-gray-400">Beneficiary: {item.beneficiary_email}</p>
          </div>
          <div className="text-right">
            <p className="mb-1 text-2xl font-bold text-gray-800">{formatCurrency(item.amount)}</p>
            <RiskBadge level={item.risk_level} />
            {item.fraud_score !== null && (
              <p className="mt-1 text-xs text-gray-500">Composite score: {item.fraud_score}</p>
            )}
            <p className="mt-1 text-xs text-gray-400">Status: {item.status}</p>
          </div>
        </div>

        <p className="mb-3 rounded border-l-4 border-blue-300 bg-gray-50 p-3 text-sm text-gray-700">
          {item.description}
        </p>

        <EngineVerdicts item={item} />

        {item.warning_flags.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {item.warning_flags.map((flag, index) => (
              <span key={index} className="rounded-full border border-orange-200 bg-orange-100 px-2 py-0.5 text-xs text-orange-800">
                ⚠ {flag}
              </span>
            ))}
          </div>
        )}

        {item.llm_explanation && (
          <div className="mb-3 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-700">LLM explanation</p>
            <p className="text-sm leading-relaxed text-slate-700">{item.llm_explanation}</p>
          </div>
        )}

        <ShapPanel topFeaturesJson={item.top_features} />

        <button
          onClick={() => setShowHistory(value => !value)}
          className="mb-3 flex items-center gap-1 text-sm text-indigo-600 hover:text-indigo-800"
        >
          {showHistory
            ? <><ChevronUp className="h-4 w-4" /> Hide pair history</>
            : <><ChevronDown className="h-4 w-4" /> Show pair history</>}
        </button>

        {showHistory && (
          <div className="mb-3 overflow-x-auto rounded-lg border border-gray-200 bg-gray-50 p-4">
            {!pairHistory ? (
              <p className="text-sm text-gray-400">Pair history is unavailable.</p>
            ) : pairHistory.history.length === 0 ? (
              <p className="text-sm text-gray-500">No other nominations exist between these employees.</p>
            ) : (
              <>
                <p className="mb-2 text-sm font-semibold text-gray-700">
                  {pairHistory.pair_count} other nomination(s) between {pairHistory.nominator_name} and {pairHistory.beneficiary_name}
                </p>
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-gray-200 text-gray-500">
                      <th className="pb-1 pr-3">Date</th><th className="pb-1 pr-3">Direction</th>
                      <th className="pb-1 pr-3">Amount</th><th className="pb-1 pr-3">Status</th>
                      <th className="pb-1 pr-3">Risk</th><th className="pb-1">Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pairHistory.history.map(row => (
                      <tr key={row.nomination_id} className="border-b border-gray-100 last:border-0">
                        <td className="whitespace-nowrap py-1.5 pr-3 text-gray-600">{new Date(row.nomination_date).toLocaleDateString()}</td>
                        <td className="whitespace-nowrap py-1.5 pr-3 text-gray-700">{row.nominator_name} → {row.beneficiary_name}</td>
                        <td className="py-1.5 pr-3 font-medium">{formatCurrency(row.amount)}</td>
                        <td className="py-1.5 pr-3">{row.status}</td>
                        <td className="py-1.5 pr-3"><RiskBadge level={row.risk_level} /></td>
                        <td className="max-w-xs truncate py-1.5 text-gray-600" title={row.description}>{row.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        )}

        <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-700">
          Read-only integrity evidence. No nomination workflow actions are available in this view.
        </div>
      </div>
    </div>
  );
};

export const ModelAnalysisTab: React.FC<Props> = ({ apiFetch, formatCurrency, onOpenNominationLogs }) => {
  const { isAdmin, isImpersonating, getEffectiveUser } = useImpersonation();
  const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
  const [subTab, setSubTab] = useState<'nominations' | 'users' | 'modelSetup' | 'elce'>('nominations');
  const [modelSetupTab, setModelSetupTab] = useState<'fraud' | 'engines'>('fraud');
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [status, setStatus] = useState('');
  const [appliedStatus, setAppliedStatus] = useState('');
  const [risk, setRisk] = useState('');
  const [appliedRisk, setAppliedRisk] = useState('');
  const [startDate, setStartDate] = useState('');
  const [appliedStartDate, setAppliedStartDate] = useState('');
  const [endDate, setEndDate] = useState(todayAsLocalDate);
  const [appliedEndDate, setAppliedEndDate] = useState('');
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<HRBPQueueItem | null>(null);
  const [detailHistory, setDetailHistory] = useState<PairHistory | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const executeSearch = async (
    targetPage: number,
    targetQuery: string,
    targetStatus: string,
    targetRisk: string,
    targetStartDate: string,
    targetEndDate: string,
  ) => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ page: String(targetPage), page_size: String(PAGE_SIZE) });
    if (targetQuery) params.set('q', targetQuery);
    if (targetStatus) params.set('status', targetStatus);
    if (targetRisk) params.set('risk', targetRisk);
    if (targetStartDate) params.set('start_date', targetStartDate);
    if (targetEndDate) params.set('end_date', targetEndDate);
    try {
      setResult(await apiFetch<SearchResponse>(`/api/model-analysis/nominations?${params}`, {}, impersonatedUPN));
      setPage(targetPage);
      setAppliedQuery(targetQuery);
      setAppliedStatus(targetStatus);
      setAppliedRisk(targetRisk);
      setAppliedStartDate(targetStartDate);
      setAppliedEndDate(targetEndDate);
      setHasSearched(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to search nominations');
    } finally {
      setLoading(false);
    }
  };

  const openDetail = async (nominationId: number) => {
    setDetailLoading(true);
    setError(null);
    try {
      const [nomination, history] = await Promise.all([
        apiFetch<HRBPQueueItem>(`/api/model-analysis/nominations/${nominationId}`, {}, impersonatedUPN),
        apiFetch<PairHistory>(`/api/model-analysis/nominations/${nominationId}/pair-history`, {}, impersonatedUPN),
      ]);
      setDetail(nomination);
      setDetailHistory(history);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load model evidence');
    } finally {
      setDetailLoading(false);
    }
  };

  const clearFilters = () => {
    setQuery('');
    setStatus('');
    setRisk('');
    setStartDate('');
    setEndDate(todayAsLocalDate());
    setAppliedQuery('');
    setAppliedStatus('');
    setAppliedRisk('');
    setAppliedStartDate('');
    setAppliedEndDate('');
    setPage(1);
    setResult(null);
    setHasSearched(false);
    setError(null);
  };

  const totalPages = Math.max(1, Math.ceil((result?.total ?? 0) / PAGE_SIZE));
  const modelSetupReadOnly = !isAdmin || isImpersonating;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-1 border-b border-gray-200 pb-3">
        <button
          onClick={() => setSubTab('nominations')}
          style={subTab === 'nominations' ? { backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' } : {}}
          className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${subTab === 'nominations' ? '' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          <FileSearch className="h-4 w-4" /> Nomination Analysis
        </button>
        <button
          onClick={() => setSubTab('users')}
          style={subTab === 'users' ? { backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' } : {}}
          className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${subTab === 'users' ? '' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          <Users className="h-4 w-4" /> User Analysis
        </button>
        <button
          onClick={() => setSubTab('modelSetup')}
          style={subTab === 'modelSetup' ? { backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' } : {}}
          className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${subTab === 'modelSetup' ? '' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          <Settings className="h-4 w-4" /> Integrity Setup
        </button>
        <button
          onClick={() => setSubTab('elce')}
          title="Even Lineage Counterfactual Explanation"
          aria-describedby="elce-description"
          style={subTab === 'elce' ? { backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' } : {}}
          className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${subTab === 'elce' ? '' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          ELCE <CircleHelp className="h-4 w-4" />
        </button>
        <span id="elce-description" className="sr-only">Even Lineage Counterfactual Explanation</span>
      </div>

      {error && <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {subTab === 'users' ? (
        <UserAnalysisTab
          key={impersonatedUPN || 'actual-user'}
          apiFetch={apiFetch}
          impersonatedUPN={impersonatedUPN}
          onOpenAnalysis={openDetail}
          onOpenLogs={onOpenNominationLogs}
        />
      ) : subTab === 'elce' ? (
        <div className="rounded-lg border border-dashed border-indigo-200 bg-indigo-50/40 px-6 py-16 text-center">
          <BrainCircuit className="mx-auto mb-3 h-12 w-12 text-indigo-300" />
          <h3 className="text-lg font-semibold text-gray-800">ELCE workspace prepared</h3>
          <p className="mt-1 text-sm text-gray-500">Even Lineage Counterfactual Explanation functionality will be added in the ELCE project.</p>
        </div>
      ) : subTab === 'modelSetup' ? (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2 rounded-lg border border-gray-200 bg-gray-50 p-2">
            <button
              onClick={() => setModelSetupTab('fraud')}
              className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${modelSetupTab === 'fraud' ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-600 hover:bg-white/70'}`}
            >
              <ShieldAlert className="h-4 w-4" /> Scoring &amp; Routing
            </button>
            <button
              onClick={() => setModelSetupTab('engines')}
              className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${modelSetupTab === 'engines' ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-600 hover:bg-white/70'}`}
            >
              <Activity className="h-4 w-4" /> Engine Status
            </button>
          </div>

          {modelSetupTab === 'fraud' ? (
            <FraudPanel
              readOnly={modelSetupReadOnly}
              endpoint={modelSetupReadOnly ? '/api/model-analysis/setup/fraud-integrity' : '/api/admin/setup/fraud'}
              impersonatedUPN={impersonatedUPN}
            />
          ) : (
            <DetectionEnginesPanel
              endpoint={modelSetupReadOnly ? '/api/model-analysis/setup/decision-engines' : '/api/admin/setup/detection-engines'}
              impersonatedUPN={impersonatedUPN}
            />
          )}
        </div>
      ) : (
        <>
          <form
            onSubmit={event => {
              event.preventDefault();
              void executeSearch(1, query.trim(), status, risk, startDate, endDate);
            }}
            className="grid grid-cols-1 items-end gap-3 rounded-lg border border-gray-200 bg-gray-50 p-4 md:grid-cols-2 xl:grid-cols-[minmax(16rem,1fr)_10.5rem_10.5rem_12rem_10.5rem_auto]"
          >
            <label className="space-y-1">
              <span className="block text-xs font-medium text-gray-600">Nomination search</span>
              <span className="relative block">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
                <input
                  value={query}
                  onChange={event => setQuery(event.target.value)}
                  placeholder="Nomination #, employee, email, or description"
                  className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </span>
            </label>
            <label className="space-y-1">
              <span className="block text-xs font-medium text-gray-600">Start date (optional)</span>
              <input type="date" value={startDate} max={endDate || undefined} onChange={event => setStartDate(event.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" />
            </label>
            <label className="space-y-1">
              <span className="block text-xs font-medium text-gray-600">End date (inclusive)</span>
              <input type="date" value={endDate} min={startDate || undefined} onChange={event => setEndDate(event.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" />
            </label>
            <label className="space-y-1">
              <span className="block text-xs font-medium text-gray-600">Status</span>
              <select value={status} onChange={event => setStatus(event.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm">
                {STATUSES.map(value => <option key={value} value={value}>{value || 'All statuses'}</option>)}
              </select>
            </label>
            <label className="space-y-1">
              <span className="block text-xs font-medium text-gray-600">Composite risk</span>
              <select value={risk} onChange={event => setRisk(event.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm">
                {RISKS.map(value => <option key={value} value={value}>{value || 'All risk levels'}</option>)}
              </select>
            </label>
            <div className="space-y-1 md:col-span-2 xl:col-span-1">
              <div className="flex h-4 items-center justify-start">
                <button disabled={loading} type="button" onClick={clearFilters} className="appearance-none border-0 bg-transparent p-0 text-xs font-medium text-indigo-600 shadow-none hover:underline disabled:opacity-50">
                  Clear filters
                </button>
              </div>
              <button disabled={loading} type="submit" style={{ backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' }} className="flex w-full items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium disabled:opacity-50">
                <Search className="h-4 w-4" /> Search
              </button>
            </div>
          </form>

          {loading && <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-400"><RefreshCw className="h-4 w-4 animate-spin" /> Searching…</div>}

          {!loading && !hasSearched && (
            <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center text-gray-400">
              <FileSearch className="mx-auto mb-3 h-12 w-12" />
              <p className="font-medium text-gray-600">Search when you are ready</p>
              <p className="mt-1 text-sm">Choose any filters, or leave them empty to search all nominations, then click Search.</p>
            </div>
          )}

          {!loading && result && result.items.length === 0 && (
            <div className="py-16 text-center text-gray-400"><FileSearch className="mx-auto mb-3 h-12 w-12" /><p>No matching nominations found.</p></div>
          )}

          {!loading && result && result.items.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-gray-200">
              <table className="w-full text-left text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="px-3 py-2">Nomination</th><th className="px-3 py-2">Date</th>
                    <th className="px-3 py-2">Nominator → Beneficiary</th><th className="px-3 py-2">Category</th>
                    <th className="px-3 py-2 text-right">Amount</th><th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Composite risk</th><th className="px-3 py-2">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {result.items.map(item => (
                    <tr key={item.nomination_id} className="hover:bg-gray-50">
                      <td className="px-3 py-2">
                        <button onClick={() => onOpenNominationLogs(item.nomination_id)} className="font-mono font-semibold text-indigo-600 hover:underline" title={`Open logs for nomination ${item.nomination_id}`}>
                          #{item.nomination_id}
                        </button>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-gray-500">{new Date(item.nomination_date).toLocaleDateString()}</td>
                      <td className="px-3 py-2 text-gray-700">{item.nominator_name} → {item.beneficiary_name}</td>
                      <td className="px-3 py-2 text-gray-500">{item.category || '—'}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right font-medium">{formatCurrency(item.amount)}</td>
                      <td className="px-3 py-2"><span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">{item.status}</span></td>
                      <td className="px-3 py-2"><RiskBadge level={item.risk_level} /></td>
                      <td className="px-3 py-2">
                        <button disabled={detailLoading} onClick={() => openDetail(item.nomination_id)} className="inline-flex items-center gap-1 text-indigo-600 hover:underline disabled:opacity-40">
                          View analysis <ExternalLink className="h-3.5 w-3.5" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!loading && result && result.total > 0 && (
            <div className="flex items-center justify-between text-sm text-gray-500">
              <span>{result.total} nomination{result.total === 1 ? '' : 's'}</span>
              <div className="flex items-center gap-2">
                <button disabled={page <= 1} onClick={() => void executeSearch(page - 1, appliedQuery, appliedStatus, appliedRisk, appliedStartDate, appliedEndDate)} className="rounded border border-gray-200 p-1.5 disabled:opacity-40" title="Previous page"><ChevronLeft className="h-4 w-4" /></button>
                <span>Page {page} of {totalPages}</span>
                <button disabled={page >= totalPages} onClick={() => void executeSearch(page + 1, appliedQuery, appliedStatus, appliedRisk, appliedStartDate, appliedEndDate)} className="rounded border border-gray-200 p-1.5 disabled:opacity-40" title="Next page"><ChevronRight className="h-4 w-4" /></button>
              </div>
            </div>
          )}
        </>
      )}

      {detailLoading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"><div className="rounded-lg bg-white px-6 py-4 shadow-xl"><RefreshCw className="mr-2 inline h-4 w-4 animate-spin" />Loading model evidence…</div></div>
      )}
      {detail && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-4 sm:p-8" role="dialog" aria-modal="true" aria-label={`Nomination analysis for nomination ${detail.nomination_id}`}>
          <div className="mx-auto max-w-7xl rounded-xl bg-white shadow-2xl">
            <div className="sticky top-0 z-10 flex items-center justify-between rounded-t-xl border-b border-gray-200 bg-white px-5 py-3">
              <div><h3 className="font-semibold text-gray-900">Nomination Analysis</h3><p className="text-xs text-gray-500">Read-only integrity evidence view</p></div>
              <button onClick={() => { setDetail(null); setDetailHistory(null); }} className="rounded p-2 text-gray-500 hover:bg-gray-100" title="Close"><X className="h-5 w-5" /></button>
            </div>
            <div className="p-4 sm:p-6"><ReadOnlyEvidence item={detail} pairHistory={detailHistory} formatCurrency={formatCurrency} /></div>
          </div>
        </div>
      )}
    </div>
  );
};
