import React, { useRef, useState } from 'react';
import { Search, Users } from 'lucide-react';
import { RiskBadge, type EngineResult } from './HRBPReviewTab';

interface User { user_id: number; name: string; email: string }
interface UserSearch { items: User[]; total: number; page: number; page_size: number }
interface Summary {
  total: number; engine_concerns: number; confirmed_issues: number;
  cleared_concerns: number; unsubstantiated: number; not_for_training: number; missing_evidence: number;
}
interface UserNomination {
  nomination_id: number; nomination_date: string; status: string;
  nominator_name: string; beneficiary_name: string; user_role: string;
  risk_level: string; composite_score: number | null; final_route: string | null;
  review_outcome: string | null; training_disposition: string | null;
  review_reason: string | null; reviewed_at: string | null; has_evidence: boolean;
  engines: Record<string, EngineResult & { concern: boolean }>;
}
interface Analysis {
  user: User; items: UserNomination[]; summary: Summary; total: number; page: number; page_size: number;
}
interface Props {
  apiFetch: <T>(path: string, options?: RequestInit, impersonatedUPN?: string) => Promise<T>;
  impersonatedUPN?: string;
  onOpenAnalysis: (id: number) => void;
  onOpenLogs: (id: number) => void;
}
const ENGINES: Record<string, string> = { rf: 'RF', graph: 'Graph Analytics', gnn: 'GNN', semantic: 'Semantic' };
const OUTCOMES: Record<string, string> = {
  CONFIRMED_CONCERN: 'Integrity concern confirmed',
  CONFIRMED_SEMANTIC_CONCERN: 'Semantic concern confirmed',
  CLEARED_NO_CONCERN: 'Cleared — no concern',
  CLEARED_UNSUBSTANTIATED: 'Cleared — concern not substantiated',
  NOT_REVIEWED: 'No recorded HRBP review',
};
const TRAINING: Record<string, string> = { FRAUD: 'Fraud label', LEGITIMATE: 'Legitimate label', EXCLUDED: 'Not for training' };
const initialFilters = { role: 'either', engine: '', risk: '', outcome: '', start_date: '', end_date: '' };
const fieldClass = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm';

export const UserAnalysisSummary: React.FC<{ summary: Summary }> = ({ summary }) => (
  <div>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {([
        ['Nominations', summary.total], ['Engine concerns', summary.engine_concerns],
        ['Human-confirmed issues', summary.confirmed_issues], ['Cleared — no concern', summary.cleared_concerns],
        ['Cleared — unsubstantiated', summary.unsubstantiated], ['Not for training', summary.not_for_training],
        ['No inference recorded', summary.missing_evidence],
      ] as const).map(([label, count]) => (
        <div key={label} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <p className="text-xs text-slate-600">{label}</p><p className="text-xl font-semibold text-slate-900">{count}</p>
        </div>
      ))}
    </div>
    <p className="mt-2 text-xs text-slate-500">Counts cover all nominations matching the applied filters, not just this page. Categories overlap. Confirmed issues include semantic concerns; “not for training” means an explicit EXCLUDED disposition, not a missing label.</p>
  </div>
);

export const UserNominationEvidence: React.FC<{ item: UserNomination }> = ({ item }) => (
  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
    {Object.entries(ENGINES).map(([key, label]) => {
      const engine = item.engines[key];
      const findings = [...(engine?.findings || []), ...(engine?.combined_decision?.checks || [])];
      return <div key={key} className="rounded border border-slate-200 p-3 text-xs">
        <p className="mb-1 font-semibold text-slate-800">{label}</p>
        {!engine?.available ? <p className="text-slate-500">Unavailable / not recorded{engine?.unavailable_reason ? `: ${engine.unavailable_reason}` : ''}</p> : <>
          <p className={engine.concern ? 'text-orange-700' : 'text-slate-600'}>
            {key === 'semantic' ? engine.combined_decision?.action || engine.status || 'Unknown' : `${engine.risk_level || 'UNKNOWN'} · Score ${engine.score ?? '—'}`}
          </p>
          {findings.length > 0 && <ul className="mt-2 space-y-1 text-orange-700">{findings.map((finding, index) => <li key={index}>• {finding}</li>)}</ul>}
          {engine.combined_decision?.reason && <p className="mt-1 text-slate-600">{engine.combined_decision.reason}</p>}
        </>}
      </div>;
    })}
  </div>
);

export const UserAnalysisTab: React.FC<Props> = ({ apiFetch, impersonatedUPN, onOpenAnalysis, onOpenLogs }) => {
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [users, setUsers] = useState<UserSearch | null>(null);
  const [selected, setSelected] = useState<User | null>(null);
  const [filters, setFilters] = useState(initialFilters);
  const [appliedFilters, setAppliedFilters] = useState(initialFilters);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const searchUsers = async (page: number, search: string) => {
    if (!search.trim()) return;
    const request = ++requestId.current;
    setLoading(true); setError(null); setSelected(null); setAnalysis(null); setUsers(null);
    try {
      const params = new URLSearchParams({ q: search, page: String(page) });
      const result = await apiFetch<UserSearch>(`/api/model-analysis/users?${params}`, {}, impersonatedUPN);
      if (request !== requestId.current) return;
      setUsers(result); setAppliedQuery(search);
    } catch (err) {
      if (request === requestId.current) setError(err instanceof Error ? err.message : 'User search failed');
    } finally { if (request === requestId.current) setLoading(false); }
  };
  const loadAnalysis = async (user: User, page: number, targetFilters: typeof initialFilters) => {
    const request = ++requestId.current;
    setLoading(true); setError(null); setSelected(user); setAnalysis(null);
    try {
      const params = new URLSearchParams({ page: String(page) });
      Object.entries(targetFilters).forEach(([key, value]) => { if (value) params.set(key, value); });
      const result = await apiFetch<Analysis>(`/api/model-analysis/users/${user.user_id}/nominations?${params}`, {}, impersonatedUPN);
      if (request !== requestId.current) return;
      setAnalysis(result); setAppliedFilters({ ...targetFilters });
    } catch (err) {
      if (request === requestId.current) setError(err instanceof Error ? err.message : 'User analysis failed');
    } finally { if (request === requestId.current) setLoading(false); }
  };
  const setFilter = (key: keyof typeof initialFilters, value: string) => setFilters(previous => ({ ...previous, [key]: value }));

  return <div className="space-y-4">
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
      Research nominations involving a user. Engine signals are not proof of wrongdoing by either participant. Human reviews and training dispositions are shown separately.
    </div>
    <form onSubmit={event => { event.preventDefault(); void searchUsers(1, query.trim()); }} className="flex items-end gap-3">
      <label className="flex-1 text-xs font-medium text-slate-600">User search
        <input required maxLength={200} value={query} onChange={event => setQuery(event.target.value)} placeholder="Name, email, or user ID" className={`${fieldClass} mt-1`} />
      </label>
      <button disabled={loading || !query.trim()} type="submit" className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50"><Search className="h-4 w-4" />Search</button>
    </form>
    {error && <p role="alert" className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    {!selected && users && <div className="space-y-2">
      <p className="text-sm text-slate-500">{users.total} matching users</p>
      {users.items.map(user => <button disabled={loading} key={user.user_id} onClick={() => { setFilters(initialFilters); void loadAnalysis(user, 1, initialFilters); }} className="block w-full rounded-lg border border-slate-200 p-3 text-left hover:bg-slate-50">
        <span className="font-medium text-indigo-700">{user.name}</span><span className="ml-2 text-xs text-slate-500">User #{user.user_id}</span>
        <span className="block text-sm text-slate-500">{user.email}</span>
      </button>)}
      <div className="flex items-center justify-end gap-3 text-sm">
        <button disabled={loading || users.page <= 1} onClick={() => void searchUsers(users.page - 1, appliedQuery)} className="text-indigo-600 disabled:opacity-40">Previous users</button>
        <span>Page {users.page} of {Math.max(1, Math.ceil(users.total / users.page_size))}</span>
        <button disabled={loading || users.page * users.page_size >= users.total} onClick={() => void searchUsers(users.page + 1, appliedQuery)} className="text-indigo-600 disabled:opacity-40">Next users</button>
      </div>
    </div>}
    {!selected && !users && !loading && <div className="py-12 text-center text-slate-500"><Users className="mx-auto mb-2 h-10 w-10" />Search for a user to explore their nomination history.</div>}
    {selected && <>
      <div className="flex items-center justify-between gap-3">
        <div><h3 className="font-semibold text-slate-900">{selected.name} · User #{selected.user_id}</h3><p className="text-sm text-slate-500">{selected.email}</p></div>
        <button onClick={() => { ++requestId.current; setLoading(false); setSelected(null); setAnalysis(null); setError(null); }} className="text-sm text-indigo-600 hover:underline">Back to users</button>
      </div>
      <form onSubmit={event => { event.preventDefault(); void loadAnalysis(selected, 1, filters); }} className="grid items-end gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 md:grid-cols-3">
        <label className="text-xs text-slate-600">User role<select value={filters.role} onChange={event => setFilter('role', event.target.value)} className={fieldClass}><option value="either">Either role</option><option value="nominator">Nominator</option><option value="nominee">Nominee</option></select></label>
        <label className="text-xs text-slate-600">Engine with concern<select value={filters.engine} onChange={event => setFilter('engine', event.target.value)} className={fieldClass}><option value="">All engines / all nominations</option>{Object.entries(ENGINES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="text-xs text-slate-600">Composite risk<select value={filters.risk} onChange={event => setFilter('risk', event.target.value)} className={fieldClass}><option value="">All risk levels</option>{['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NONE', 'UNKNOWN'].map(risk => <option key={risk}>{risk}</option>)}</select></label>
        <label className="text-xs text-slate-600">HRBP outcome<select value={filters.outcome} onChange={event => setFilter('outcome', event.target.value)} className={fieldClass}><option value="">All outcomes</option>{Object.entries(OUTCOMES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="text-xs text-slate-600">Start date<input type="date" value={filters.start_date} max={filters.end_date || undefined} onChange={event => setFilter('start_date', event.target.value)} className={fieldClass} /></label>
        <label className="text-xs text-slate-600">End date (inclusive)<input type="date" value={filters.end_date} min={filters.start_date || undefined} onChange={event => setFilter('end_date', event.target.value)} className={fieldClass} /></label>
        <div className="flex gap-3 md:col-span-3"><button disabled={loading} type="submit" className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50">Apply filters</button><button disabled={loading} type="button" onClick={() => { setFilters(initialFilters); void loadAnalysis(selected, 1, initialFilters); }} className="text-sm text-indigo-600 disabled:opacity-50">Clear filters</button></div>
      </form>
      <p className="text-xs text-slate-500">An engine concern means an available engine reported a finding, LOW-or-higher risk, or a semantic flag/rejection. Risk filtering uses the composite nomination risk.</p>
    </>}
    {loading && <p role="status" className="py-8 text-center text-slate-500">Loading…</p>}
    {!loading && analysis && <>
      <UserAnalysisSummary summary={analysis.summary} />
      {analysis.items.length === 0 && <p className="py-8 text-center text-slate-500">No nominations match these filters.</p>}
      {analysis.items.map(item => <article key={item.nomination_id} className="space-y-3 rounded-lg border border-slate-200 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><button onClick={() => onOpenAnalysis(item.nomination_id)} className="font-semibold text-indigo-700 hover:underline">Nomination #{item.nomination_id} · View analysis</button>
            <p className="text-sm text-slate-700">{item.nominator_name} → {item.beneficiary_name}</p>
            <p className="text-xs text-slate-500">{new Date(item.nomination_date).toLocaleDateString()} · User role: {item.user_role} · Status: {item.status}</p>
          </div>
          <div className="text-right"><RiskBadge level={item.risk_level} /><p className="text-xs text-slate-500">Composite score: {item.composite_score ?? 'Not recorded'}</p><button onClick={() => onOpenLogs(item.nomination_id)} className="text-xs text-indigo-600 hover:underline">View logs</button></div>
        </div>
        <UserNominationEvidence item={item} />
        <div className="rounded bg-slate-50 p-3 text-sm text-slate-700">
          <p><span className="font-medium">HRBP:</span> {item.review_outcome ? OUTCOMES[item.review_outcome] || item.review_outcome : OUTCOMES.NOT_REVIEWED}</p>
          <p><span className="font-medium">Training:</span> {item.training_disposition ? TRAINING[item.training_disposition] || item.training_disposition : 'No recorded disposition'}</p>
          {item.review_reason && <p className="mt-1">Review reason: {item.review_reason}</p>}
          {item.reviewed_at && <p className="text-xs text-slate-500">Reviewed: {new Date(item.reviewed_at).toLocaleString()}</p>}
          <p className="text-xs text-slate-500">Recorded inference route: {item.final_route || 'Not recorded'}</p>
        </div>
      </article>)}
      {analysis.total > 0 && <div className="flex items-center justify-end gap-3 text-sm"><button disabled={analysis.page <= 1} onClick={() => void loadAnalysis(analysis.user, analysis.page - 1, appliedFilters)} className="text-indigo-600 disabled:opacity-40">Previous nominations</button><span>Page {analysis.page} of {Math.max(1, Math.ceil(analysis.total / analysis.page_size))}</span><button disabled={analysis.page * analysis.page_size >= analysis.total} onClick={() => void loadAnalysis(analysis.user, analysis.page + 1, appliedFilters)} className="text-indigo-600 disabled:opacity-40">Next nominations</button></div>}
    </>}
  </div>;
};
