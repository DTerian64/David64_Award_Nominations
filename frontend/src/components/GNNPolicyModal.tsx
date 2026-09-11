import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle, RefreshCw, Save, X } from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type Risk = 'NONE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
type Architecture = 'graphsage' | 'gcn' | 'gatv2';

interface GNNPolicy {
  policy_id: number;
  policy_version: number;
  status: 'DRAFT' | 'ACTIVE' | 'RETIRED';
  training_enabled: boolean;
  inference_enabled: boolean;
  hidden_dim: number;
  embed_dim: number;
  epochs: number;
  rolling_folds: number;
  window_days: number;
  embedding_retention_days: number;
  stale_embedding_days: number;
  minimum_training_samples: number;
  minimum_users: number;
  minimum_positives_per_split: number;
  candidate_architectures: Architecture[];
  selection_metric: 'holdout_pr_auc';
  minimum_improvement_over_mlp: number;
  incumbent_tie_tolerance: number;
  minimum_eligible_graph_candidates: number;
  thresholds: { low: number; medium: number; high: number; critical: number };
  explanation_enabled: boolean;
  explanation_minimum_risk: Risk;
  published_at: string | null;
  published_by: string | null;
}

interface PolicyBundle {
  active_policy: GNNPolicy | null;
  draft_policy: GNNPolicy | null;
  history: GNNPolicy[];
  can_edit: boolean;
}

interface Props {
  impersonatedUPN?: string;
  onClose: () => void;
}

const ARCHITECTURES: { value: Architecture; label: string }[] = [
  { value: 'graphsage', label: 'GraphSAGE' },
  { value: 'gcn', label: 'GCN' },
  { value: 'gatv2', label: 'GATv2' },
];

const NumberField: React.FC<{
  label: string;
  value: number;
  disabled: boolean;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}> = ({ label, value, disabled, min = 0, max, step = 1, onChange }) => (
  <label className="block text-xs text-gray-500">
    {label}
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onChange={event => onChange(Number(event.target.value))}
      className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-2 text-sm text-gray-800 disabled:bg-gray-50 disabled:text-gray-500"
    />
  </label>
);

const fmtTime = (value: string | null) => value
  ? new Date(value).toLocaleString()
  : '—';

export const GNNPolicyModal: React.FC<Props> = ({ impersonatedUPN, onClose }) => {
  const [bundle, setBundle] = useState<PolicyBundle | null>(null);
  const [draft, setDraft] = useState<GNNPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  const headers = useCallback(async (json = false) => ({
    Authorization: `Bearer ${await getAccessToken()}`,
    ...(json ? { 'Content-Type': 'application/json' } : {}),
    ...(impersonatedUPN ? { 'X-Impersonate-User': impersonatedUPN } : {}),
  }), [impersonatedUPN]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/model-analysis/setup/gnn-policy`,
        { headers: await headers() },
      );
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
      const value: PolicyBundle = await response.json();
      setBundle(value);
      setDraft(value.draft_policy ? { ...value.draft_policy } : null);
    } catch (error: unknown) {
      setMessage({ type: 'err', text: error instanceof Error ? error.message : 'Failed to load GNN policy' });
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => { void load(); }, [load]);

  const mutate = async (path: string, method: string, body?: unknown) => {
    setSaving(true);
    setMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        method,
        headers: await headers(body !== undefined),
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `HTTP ${response.status}`);
      setMessage({ type: 'ok', text: value.message || 'GNN policy updated.' });
      await load();
    } catch (error: unknown) {
      setMessage({ type: 'err', text: error instanceof Error ? error.message : 'GNN policy update failed' });
    } finally {
      setSaving(false);
    }
  };

  const policy = draft || bundle?.active_policy || null;
  const edit = (patch: Partial<GNNPolicy>) => setDraft(current => current ? { ...current, ...patch } : current);
  const threshold = (key: keyof GNNPolicy['thresholds'], value: number) => setDraft(current => current ? {
    ...current,
    thresholds: { ...current.thresholds, [key]: value },
  } : current);
  const toggleArchitecture = (architecture: Architecture) => setDraft(current => {
    if (!current) return current;
    const selected = current.candidate_architectures.includes(architecture)
      ? current.candidate_architectures.filter(value => value !== architecture)
      : [...current.candidate_architectures, architecture];
    return { ...current, candidate_architectures: selected };
  });

  const savePayload = draft && {
    training_enabled: draft.training_enabled,
    inference_enabled: draft.inference_enabled,
    hidden_dim: draft.hidden_dim,
    embed_dim: draft.embed_dim,
    epochs: draft.epochs,
    rolling_folds: draft.rolling_folds,
    window_days: draft.window_days,
    embedding_retention_days: draft.embedding_retention_days,
    stale_embedding_days: draft.stale_embedding_days,
    minimum_training_samples: draft.minimum_training_samples,
    minimum_users: draft.minimum_users,
    minimum_positives_per_split: draft.minimum_positives_per_split,
    candidate_architectures: draft.candidate_architectures,
    selection_metric: draft.selection_metric,
    minimum_improvement_over_mlp: draft.minimum_improvement_over_mlp,
    incumbent_tie_tolerance: draft.incumbent_tie_tolerance,
    minimum_eligible_graph_candidates: draft.minimum_eligible_graph_candidates,
    thresholds: draft.thresholds,
    explanation_enabled: draft.explanation_enabled,
    explanation_minimum_risk: draft.explanation_minimum_risk,
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="GNN policy">
      <div className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <header className="flex items-start justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">GNN policy</h2>
            <p className="mt-1 text-sm text-gray-500">Versioned tenant settings for GNN training, architecture selection, live scoring, and explanation.</p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100" aria-label="Close"><X className="h-5 w-5" /></button>
        </header>

        <div className="overflow-y-auto p-5">
          {loading && <div className="flex justify-center gap-2 py-16 text-sm text-gray-400"><RefreshCw className="h-4 w-4 animate-spin" />Loading…</div>}
          {!loading && !policy && <div className="rounded-md bg-amber-50 p-4 text-sm text-amber-800">No active GNN policy exists. Apply schema migration 0059 before using this page.</div>}
          {!loading && policy && (
            <div className="space-y-5">
              <section className="rounded-lg border border-violet-100 bg-violet-50/40 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-semibold text-violet-900">{draft ? `Draft policy v${draft.policy_version}` : `Active policy v${policy.policy_version}`}</h3>
                    <p className="mt-1 text-xs text-violet-700">Serving controls take effect on the next nomination. Training controls are read fresh when the analytics job next reaches this tenant.</p>
                  </div>
                  <div className="flex gap-2">
                    {bundle?.can_edit && !draft && <button disabled={saving} onClick={() => void mutate('/api/admin/setup/gnn-policy/draft', 'POST')} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">Create draft</button>}
                    {bundle?.can_edit && draft && <>
                      <button disabled={saving} onClick={() => savePayload && void mutate('/api/admin/setup/gnn-policy/draft', 'PUT', savePayload)} className="inline-flex items-center gap-1 rounded-md border border-indigo-200 px-3 py-2 text-sm font-medium text-indigo-700 disabled:opacity-50"><Save className="h-4 w-4" />Save draft</button>
                      <button disabled={saving} onClick={() => void mutate('/api/admin/setup/gnn-policy/draft/publish', 'POST')} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">Publish</button>
                    </>}
                  </div>
                </div>
              </section>

              <section className="rounded-lg border border-gray-200 p-4">
                <h3 className="text-sm font-semibold text-gray-800">Operational controls</h3>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" disabled={!draft} checked={policy.training_enabled} onChange={event => edit({ training_enabled: event.target.checked })} />Run GNN candidate training</label>
                  <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" disabled={!draft} checked={policy.inference_enabled} onChange={event => edit({ inference_enabled: event.target.checked })} />Use the selected GNN in live scoring</label>
                </div>
              </section>

              <section className="rounded-lg border border-gray-200 p-4">
                <h3 className="text-sm font-semibold text-gray-800">Training and data gates</h3>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <NumberField label="Hidden dimension" value={policy.hidden_dim} disabled={!draft} min={1} onChange={value => edit({ hidden_dim: value })} />
                  <NumberField label="Embedding dimension" value={policy.embed_dim} disabled={!draft} min={1} onChange={value => edit({ embed_dim: value })} />
                  <NumberField label="Epochs" value={policy.epochs} disabled={!draft} min={1} onChange={value => edit({ epochs: value })} />
                  <NumberField label="Rolling folds" value={policy.rolling_folds} disabled={!draft} min={2} onChange={value => edit({ rolling_folds: value })} />
                  <NumberField label="History window (days)" value={policy.window_days} disabled={!draft} min={1} onChange={value => edit({ window_days: value })} />
                  <NumberField label="Embedding retention (days)" value={policy.embedding_retention_days} disabled={!draft} min={1} onChange={value => edit({ embedding_retention_days: value })} />
                  <NumberField label="Stale after (days)" value={policy.stale_embedding_days} disabled={!draft} min={1} onChange={value => edit({ stale_embedding_days: value })} />
                  <NumberField label="Minimum nominations" value={policy.minimum_training_samples} disabled={!draft} min={1} onChange={value => edit({ minimum_training_samples: value })} />
                  <NumberField label="Minimum users" value={policy.minimum_users} disabled={!draft} min={1} onChange={value => edit({ minimum_users: value })} />
                  <NumberField label="Minimum fraud labels per split" value={policy.minimum_positives_per_split} disabled={!draft} min={1} onChange={value => edit({ minimum_positives_per_split: value })} />
                </div>
              </section>

              <section className="rounded-lg border border-gray-200 p-4">
                <h3 className="text-sm font-semibold text-gray-800">Operational architecture selection</h3>
                <p className="mt-1 text-xs text-gray-500">MLP remains the admission baseline. Only a graph candidate can become the live GNN architecture.</p>
                <div className="mt-3 flex flex-wrap gap-4">
                  {ARCHITECTURES.map(item => <label key={item.value} className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" disabled={!draft} checked={policy.candidate_architectures.includes(item.value)} onChange={() => toggleArchitecture(item.value)} />{item.label}</label>)}
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  <NumberField label="Minimum improvement over MLP" value={policy.minimum_improvement_over_mlp} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ minimum_improvement_over_mlp: value })} />
                  <NumberField label="Incumbent tie tolerance" value={policy.incumbent_tie_tolerance} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ incumbent_tie_tolerance: value })} />
                  <NumberField label="Minimum eligible graph candidates" value={policy.minimum_eligible_graph_candidates} disabled={!draft} min={1} max={policy.candidate_architectures.length || 1} onChange={value => edit({ minimum_eligible_graph_candidates: value })} />
                </div>
                <p className="mt-3 text-xs text-gray-500">Selection metric: holdout PR-AUC</p>
              </section>

              <section className="rounded-lg border border-gray-200 p-4">
                <h3 className="text-sm font-semibold text-gray-800">Live score routing (0–100)</h3>
                <div className="mt-3 grid gap-3 sm:grid-cols-4">
                  {(['low', 'medium', 'high', 'critical'] as const).map(key => <NumberField key={key} label={`${key[0].toUpperCase()}${key.slice(1)} threshold`} value={policy.thresholds[key]} disabled={!draft} min={0} max={100} step={0.01} onChange={value => threshold(key, value)} />)}
                </div>
              </section>

              <section className="rounded-lg border border-gray-200 p-4">
                <h3 className="text-sm font-semibold text-gray-800">GNN explanation</h3>
                <div className="mt-3 flex flex-wrap items-center gap-5">
                  <label className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" disabled={!draft} checked={policy.explanation_enabled} onChange={event => edit({ explanation_enabled: event.target.checked })} />Request asynchronous GNNExplainer evidence</label>
                  <label className="text-xs text-gray-500">Minimum risk
                    <select disabled={!draft || !policy.explanation_enabled} value={policy.explanation_minimum_risk} onChange={event => edit({ explanation_minimum_risk: event.target.value as Risk })} className="ml-2 rounded-md border border-gray-300 px-2.5 py-2 text-sm text-gray-800 disabled:bg-gray-50">
                      {(['NONE', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as Risk[]).map(value => <option key={value}>{value}</option>)}
                    </select>
                  </label>
                </div>
              </section>

              {bundle && bundle.history.length > 0 && <section>
                <h3 className="text-sm font-semibold text-gray-800">Policy history</h3>
                <div className="mt-2 divide-y divide-gray-100 rounded-lg border border-gray-200">
                  {bundle.history.map(item => <div key={item.policy_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs"><span className="font-medium text-gray-700">Version {item.policy_version}</span><span className="text-gray-500">{item.status}</span><span className="text-gray-400">Published {fmtTime(item.published_at)}</span></div>)}
                </div>
              </section>}
            </div>
          )}

          {message && <div className={`mt-4 flex items-start gap-2 rounded-md p-3 text-sm ${message.type === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{message.type === 'ok' ? <CheckCircle className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}<span>{message.text}</span></div>}
        </div>
      </div>
    </div>
  );
};
