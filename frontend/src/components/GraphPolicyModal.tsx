import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle, RefreshCw, Save, Send, X } from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface PatternPolicy {
  pattern_type: string;
  enabled: boolean;
  enabled_for_routing: boolean;
  applicable_roles: string[];
  base_score: number;
  minimum_score: number;
  maximum_score: number;
  parameters: Record<string, number>;
}

interface GraphPolicy {
  policy_id: number;
  policy_version: number;
  status: 'DRAFT' | 'ACTIVE' | 'RETIRED';
  scoring_strategy: string;
  thresholds: { low: number; medium: number; high: number; critical: number };
  detection_window_days: number;
  snapshot_max_age_days: number;
  patterns: PatternPolicy[];
  published_at: string | null;
  published_by: string | null;
}

interface ChangeRequest {
  request_id: number;
  pattern_type: string | null;
  request_text: string;
  status: string;
  requested_at: string;
  requested_by: string;
  admin_response: string | null;
  suggested_parameters: Record<string, unknown> | null;
  supporting_nomination_ids: number[];
}

interface PolicyBundle {
  active_policy: GraphPolicy | null;
  draft_policy: GraphPolicy | null;
  history: GraphPolicy[];
  requests: ChangeRequest[];
  can_edit: boolean;
  can_request: boolean;
}

interface Props {
  impersonatedUPN?: string;
  onClose: () => void;
}

const label = (value: string) =>
  value.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase());

const requestHeaders = async (impersonatedUPN?: string) => {
  const token = await getAccessToken();
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
  if (impersonatedUPN) headers['X-Impersonate-User'] = impersonatedUPN;
  return headers;
};

const NumberInput: React.FC<{
  labelText: string;
  value: number;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}> = ({ labelText, value, disabled, min = 0, max, step = 1, onChange }) => (
  <label className="block text-xs text-gray-500">
    {labelText}
    <input
      type="number"
      value={value}
      disabled={disabled}
      min={min}
      max={max}
      step={step}
      onChange={event => onChange(Number(event.target.value))}
      className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-2 text-sm text-gray-800 disabled:bg-gray-50 disabled:text-gray-500"
    />
  </label>
);

export const GraphPolicyModal: React.FC<Props> = ({ impersonatedUPN, onClose }) => {
  const [bundle, setBundle] = useState<PolicyBundle | null>(null);
  const [draft, setDraft] = useState<GraphPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [requestPattern, setRequestPattern] = useState('');
  const [requestText, setRequestText] = useState('');
  const [requestNominations, setRequestNominations] = useState('');
  const [requestProposal, setRequestProposal] = useState('');
  const [simPattern, setSimPattern] = useState('');
  const [simSignals, setSimSignals] = useState<Record<string, number>>({});
  const [reviewResponses, setReviewResponses] = useState<Record<number, string>>({});

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/model-analysis/setup/graph-policy`,
        { headers: await requestHeaders(impersonatedUPN) },
      );
      if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(body?.detail || `HTTP ${response.status}`);
      }
      const next = await response.json() as PolicyBundle;
      setBundle(next);
      setDraft(next.draft_policy ? structuredClone(next.draft_policy) : null);
      if (!simPattern && next.active_policy?.patterns[0]) {
        setSimPattern(next.active_policy.patterns[0].pattern_type);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load the scoring policy');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const policy = draft || bundle?.active_policy || null;
  const simulationPattern = useMemo(
    () => bundle?.active_policy?.patterns.find(item => item.pattern_type === simPattern),
    [bundle, simPattern],
  );
  const simulatedScore = useMemo(() => {
    if (!simulationPattern) return 0;
    const weighted = Object.entries(simulationPattern.parameters)
      .filter(([key]) => key.endsWith('_weight'))
      .reduce((sum, [key, weight]) => {
        const signal = key.slice(0, -'_weight'.length);
        return sum + Number(weight) * ((simSignals[signal] || 0) / 100);
      }, 0);
    return Math.max(
      simulationPattern.minimum_score,
      Math.min(simulationPattern.maximum_score, simulationPattern.base_score + weighted),
    );
  }, [simulationPattern, simSignals]);

  const mutate = async (path: string, method: string, body?: unknown) => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        method,
        headers: await requestHeaders(impersonatedUPN),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const result = await response.json().catch(() => ({})) as { detail?: string; message?: string };
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      setMessage(result.message || 'Saved');
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The change could not be saved');
    } finally {
      setSaving(false);
    }
  };

  const createDraft = () => mutate('/api/admin/setup/graph-policy/draft', 'POST');
  const saveDraft = () => draft && mutate('/api/admin/setup/graph-policy/draft', 'PUT', {
    thresholds: draft.thresholds,
    detection_window_days: draft.detection_window_days,
    snapshot_max_age_days: draft.snapshot_max_age_days,
    patterns: draft.patterns,
  });
  const publishDraft = () => {
    if (window.confirm('Publish this policy version? It will be used by the next weekly Graph Analytics run.')) {
      void mutate('/api/admin/setup/graph-policy/draft/publish', 'POST');
    }
  };

  const updateThreshold = (key: keyof GraphPolicy['thresholds'], value: number) => {
    setDraft(current => current ? {
      ...current, thresholds: { ...current.thresholds, [key]: value },
    } : current);
  };

  const updatePattern = (index: number, update: Partial<PatternPolicy>) => {
    setDraft(current => current ? {
      ...current,
      patterns: current.patterns.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...update } : item),
    } : current);
  };

  const submitRequest = async () => {
    const nominations = requestNominations.split(',')
      .map(value => Number(value.trim())).filter(value => Number.isInteger(value) && value > 0);
    await mutate('/api/model-analysis/setup/graph-policy/requests', 'POST', {
      pattern_type: requestPattern || null,
      request_text: requestText,
      supporting_nomination_ids: nominations,
      suggested_parameters: requestProposal.trim()
        ? { proposal: requestProposal.trim() }
        : null,
    });
    setRequestText('');
    setRequestNominations('');
    setRequestProposal('');
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label="Graph Analytics scoring policy">
      <div className="mx-auto max-w-7xl overflow-hidden rounded-xl bg-white shadow-2xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">Graph Analytics scoring policy</h3>
            <p className="mt-0.5 text-xs text-gray-500">Continuous finding scores and the Maximum relevant finding strategy</p>
          </div>
          <button onClick={onClose} className="rounded p-2 text-gray-500 hover:bg-gray-100" title="Close scoring policy" aria-label="Close scoring policy"><X className="h-5 w-5" /></button>
        </header>

        <div className="space-y-5 p-4 sm:p-6">
          {loading && <div className="flex items-center justify-center gap-2 py-20 text-sm text-gray-400"><RefreshCw className="h-4 w-4 animate-spin" />Loading scoring policy…</div>}
          {error && <div className="flex items-start gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-700"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
          {message && <div className="flex items-start gap-2 rounded-lg bg-green-50 p-3 text-sm text-green-700"><CheckCircle className="mt-0.5 h-4 w-4 shrink-0" />{message}</div>}
          {!loading && !error && !policy && <div className="rounded-lg border border-dashed border-gray-200 px-6 py-12 text-center text-sm text-gray-500">No Graph Analytics scoring policy is available for this organization.</div>}

          {!loading && policy && (
            <>
              <section className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h4 className="font-semibold text-gray-800">Version {policy.policy_version} · {policy.status === 'DRAFT' ? 'Draft' : 'Active'}</h4>
                    <p className="mt-1 text-sm text-gray-600"><strong>Maximum relevant finding:</strong> the nomination receives the highest applicable routing-enabled finding score.</p>
                  </div>
                  {bundle?.can_edit && !draft && <button onClick={createDraft} disabled={saving} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Create draft</button>}
                  {bundle?.can_edit && draft && (
                    <div className="flex gap-2">
                      <button onClick={saveDraft} disabled={saving} className="inline-flex items-center gap-1 rounded-md border border-indigo-200 px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50"><Save className="h-4 w-4" />Save draft</button>
                      <button onClick={publishDraft} disabled={saving} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Publish</button>
                    </div>
                  )}
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
                  {(['low', 'medium', 'high', 'critical'] as const).map(key => (
                    <NumberInput key={key} labelText={label(`${key} threshold`)} value={policy.thresholds[key]} disabled={!draft} max={100} step={0.01} onChange={value => updateThreshold(key, value)} />
                  ))}
                  <NumberInput labelText="Detection window (days)" value={policy.detection_window_days} disabled={!draft} min={1} onChange={value => setDraft(current => current ? { ...current, detection_window_days: value } : current)} />
                  <NumberInput labelText="Maximum snapshot age (days)" value={policy.snapshot_max_age_days} disabled={!draft} min={1} onChange={value => setDraft(current => current ? { ...current, snapshot_max_age_days: value } : current)} />
                </div>
              </section>

              <section>
                <h4 className="mb-2 text-sm font-semibold text-gray-700">Detector scoring</h4>
                <div className="space-y-3">
                  {policy.patterns.map((pattern, index) => (
                    <details key={pattern.pattern_type} className="rounded-lg border border-gray-200 p-4">
                      <summary className="cursor-pointer list-none">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <strong className="text-sm text-gray-800">{label(pattern.pattern_type)}</strong>
                          <span className={`rounded-full px-2 py-0.5 text-xs ${pattern.enabled_for_routing ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-600'}`}>{pattern.enabled_for_routing ? 'Used for routing' : 'Analytics only'}</span>
                        </div>
                      </summary>
                      <div className="mt-4 space-y-4">
                        <p className="text-xs text-gray-500">Finding score = base score + each normalized evidence signal × its weight, limited to the configured minimum and maximum.</p>
                        <div className="flex flex-wrap gap-4 text-sm">
                          <label className="flex items-center gap-2"><input type="checkbox" checked={pattern.enabled} disabled={!draft} onChange={event => updatePattern(index, { enabled: event.target.checked, enabled_for_routing: event.target.checked ? pattern.enabled_for_routing : false })} />Detection enabled</label>
                          <label className="flex items-center gap-2"><input type="checkbox" checked={pattern.enabled_for_routing} disabled={!draft || !pattern.enabled} onChange={event => updatePattern(index, { enabled_for_routing: event.target.checked })} />Use for nomination routing</label>
                          {(['nominator', 'beneficiary'] as const).map(role => (
                            <label key={role} className="flex items-center gap-2"><input type="checkbox" checked={pattern.applicable_roles.includes(role)} disabled={!draft} onChange={event => updatePattern(index, { applicable_roles: event.target.checked ? [...pattern.applicable_roles, role] : pattern.applicable_roles.filter(value => value !== role) })} />{label(role)}</label>
                          ))}
                        </div>
                        <div className="grid gap-3 sm:grid-cols-3">
                          <NumberInput labelText="Base score" value={pattern.base_score} disabled={!draft} max={100} step={0.01} onChange={value => updatePattern(index, { base_score: value })} />
                          <NumberInput labelText="Minimum score" value={pattern.minimum_score} disabled={!draft} max={100} step={0.01} onChange={value => updatePattern(index, { minimum_score: value })} />
                          <NumberInput labelText="Maximum score" value={pattern.maximum_score} disabled={!draft} max={100} step={0.01} onChange={value => updatePattern(index, { maximum_score: value })} />
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                          {Object.entries(pattern.parameters).map(([key, value]) => (
                            <NumberInput key={key} labelText={label(key)} value={value} disabled={!draft} step={key.includes('threshold') || key.includes('deviation') ? 0.01 : 1} onChange={next => updatePattern(index, { parameters: { ...pattern.parameters, [key]: next } })} />
                          ))}
                        </div>
                      </div>
                    </details>
                  ))}
                </div>
              </section>

              <section className="rounded-lg border border-blue-100 bg-blue-50/40 p-4">
                <h4 className="text-sm font-semibold text-gray-700">Score simulator</h4>
                <p className="mt-1 text-xs text-gray-500">Explore the active policy without changing any values.</p>
                <div className="mt-3 grid gap-4 lg:grid-cols-[16rem_1fr_8rem]">
                  <select value={simPattern} onChange={event => { setSimPattern(event.target.value); setSimSignals({}); }} className="rounded-md border border-gray-300 px-3 py-2 text-sm">
                    {bundle?.active_policy?.patterns.map(item => <option key={item.pattern_type} value={item.pattern_type}>{label(item.pattern_type)}</option>)}
                  </select>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {Object.keys(simulationPattern?.parameters || {}).filter(key => key.endsWith('_weight')).map(key => {
                      const signal = key.slice(0, -'_weight'.length);
                      return <label key={signal} className="text-xs text-gray-500">{label(signal)} evidence: {simSignals[signal] || 0}%<input type="range" min="0" max="100" value={simSignals[signal] || 0} onChange={event => setSimSignals(current => ({ ...current, [signal]: Number(event.target.value) }))} className="block w-full" /></label>;
                    })}
                  </div>
                  <div className="rounded-lg bg-white p-3 text-center"><div className="text-xs text-gray-400">Finding score</div><div className="text-2xl font-bold text-indigo-700">{simulatedScore.toFixed(2)}</div></div>
                </div>
              </section>

              {bundle?.can_request && <section className="rounded-lg border border-gray-200 p-4">
                <h4 className="text-sm font-semibold text-gray-700">Request fine-tuning</h4>
                <div className="mt-3 grid gap-3 lg:grid-cols-4">
                  <select value={requestPattern} onChange={event => setRequestPattern(event.target.value)} className="rounded-md border border-gray-300 px-3 py-2 text-sm"><option value="">Entire policy</option>{policy.patterns.map(item => <option key={item.pattern_type} value={item.pattern_type}>{label(item.pattern_type)}</option>)}</select>
                  <input value={requestNominations} onChange={event => setRequestNominations(event.target.value)} placeholder="Nomination numbers (optional)" className="rounded-md border border-gray-300 px-3 py-2 text-sm" />
                  <textarea value={requestText} onChange={event => setRequestText(event.target.value)} placeholder="Describe the observed issue and desired outcome" className="min-h-20 rounded-md border border-gray-300 px-3 py-2 text-sm lg:col-span-2" />
                  <textarea value={requestProposal} onChange={event => setRequestProposal(event.target.value)} placeholder="Suggested parameter changes (optional)" className="min-h-16 rounded-md border border-gray-300 px-3 py-2 text-sm lg:col-span-4" />
                </div>
                <button onClick={() => void submitRequest()} disabled={saving || !requestText.trim()} className="mt-3 inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"><Send className="h-4 w-4" />Submit request</button>
              </section>}

              <section className="grid gap-4 lg:grid-cols-2">
                <div>
                  <h4 className="mb-2 text-sm font-semibold text-gray-700">Fine-tuning requests</h4>
                  <div className="max-h-64 space-y-2 overflow-y-auto">
                    {(bundle?.requests || []).map(item => (
                      <div key={item.request_id} className="rounded-lg border border-gray-200 p-3 text-xs">
                        <div className="flex justify-between gap-2"><strong>#{item.request_id} · {item.pattern_type ? label(item.pattern_type) : 'Entire policy'}</strong><span>{label(item.status)}</span></div>
                        <p className="mt-1 text-gray-600">{item.request_text}</p>
                        {item.supporting_nomination_ids.length > 0 && <p className="mt-1 text-gray-500">Examples: {item.supporting_nomination_ids.map(value => `#${value}`).join(', ')}</p>}
                        {item.suggested_parameters?.proposal != null && <p className="mt-1 text-gray-500">Suggestion: {String(item.suggested_parameters.proposal)}</p>}
                        {item.admin_response && <p className="mt-1 rounded bg-gray-50 p-2 text-gray-600"><strong>Admin:</strong> {item.admin_response}</p>}
                        <p className="mt-1 text-gray-400">{item.requested_by} · {new Date(item.requested_at).toLocaleString()}</p>
                        {bundle?.can_edit && item.status !== 'PUBLISHED' && item.status !== 'REJECTED' && (
                          <div className="mt-2 space-y-2">
                            <input value={reviewResponses[item.request_id] || ''} onChange={event => setReviewResponses(current => ({ ...current, [item.request_id]: event.target.value }))} placeholder="Admin response (optional)" className="w-full rounded border border-gray-200 px-2 py-1.5 text-xs" />
                            <div className="flex gap-2">
                              {(item.status === 'REQUESTED'
                                ? ['UNDER_REVIEW', 'APPROVED', 'REJECTED']
                                : item.status === 'UNDER_REVIEW'
                                  ? ['APPROVED', 'REJECTED']
                                  : ['PUBLISHED', 'REJECTED']
                              ).map(status => <button key={status} onClick={() => void mutate(`/api/admin/setup/graph-policy/requests/${item.request_id}`, 'PATCH', { status, admin_response: reviewResponses[item.request_id] || null })} className="text-indigo-600 hover:underline">{label(status)}</button>)}
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                    {(bundle?.requests || []).length === 0 && <p className="text-xs text-gray-400">No requests submitted.</p>}
                  </div>
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-semibold text-gray-700">Version history</h4>
                  <div className="space-y-2">
                    {(bundle?.history || []).map(item => <div key={item.policy_id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-xs"><span>Version {item.policy_version}</span><span>{label(item.status)}{item.published_at ? ` · ${new Date(item.published_at).toLocaleDateString()}` : ''}</span></div>)}
                  </div>
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
