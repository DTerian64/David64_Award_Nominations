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
  selection_metric: 'holdout_pr_auc' | 'validation_overall_pr_auc';
  minimum_improvement_over_mlp: number;
  incumbent_tie_tolerance: number;
  minimum_eligible_graph_candidates: number;
  thresholds: { low: number; medium: number; high: number; critical: number };
  explanation_enabled: boolean;
  explanation_minimum_risk: Risk;
  serving_mode: 'single_winner_v2' | 'scenario_specialists' | 'shared_encoder_multi_head';
  behavior_tracks: Record<string, Record<string, unknown>>;
  aggregation: Record<string, unknown>;
  overall_loss_weight: number;
  pattern_total_loss_weight: number;
  overall_tie_tolerance: number;
  minimum_graph_value_over_raw_mlp: number;
  minimum_message_passing_value_over_engineered_graph_mlp: number;
  pattern_heads: Record<string, { enabled: boolean; feature_contract: string }>;
  maximum_holdout_inference_ms: number;
  maximum_overall_holdout_brier_score: number;
  minimum_pattern_train_positives: number;
  minimum_pattern_validation_positives: number;
  minimum_pattern_holdout_positives: number;
  minimum_pattern_holdout_negatives: number;
  maximum_pattern_holdout_brier_score: number;
  maximum_pattern_validation_pr_auc_range: number;
  minimum_pattern_improvement_over_engineered_mlp: number;
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

const V4_HEADS: Record<string, string> = {
  RECIPROCAL: 'reciprocal-v1',
  RING: 'ring-v1',
  TEMPORAL_BURST: 'temporal-burst-v1',
  SUPER_NOMINATOR: 'super-nominator-v1',
  SUPER_BENEFICIARY: 'super-beneficiary-v1',
  BIPARTITE_DENSE_BLOCK: 'bipartite-dense-block-v1',
};

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
  const changeServingMode = (mode: GNNPolicy['serving_mode']) => setDraft(current => current ? {
    ...current,
    serving_mode: mode,
    selection_metric: mode === 'shared_encoder_multi_head'
      ? 'validation_overall_pr_auc' : 'holdout_pr_auc',
    pattern_heads: mode === 'shared_encoder_multi_head'
      ? Object.fromEntries(Object.entries(V4_HEADS).map(([key, feature_contract]) => [
          key, current.pattern_heads?.[key] || { enabled: true, feature_contract },
        ]))
      : current.pattern_heads,
  } : current);

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
    serving_mode: draft.serving_mode,
    behavior_tracks: draft.behavior_tracks,
    aggregation: draft.aggregation,
    overall_loss_weight: draft.overall_loss_weight ?? 1,
    pattern_total_loss_weight: draft.pattern_total_loss_weight ?? 1,
    overall_tie_tolerance: draft.overall_tie_tolerance ?? 0.01,
    minimum_graph_value_over_raw_mlp: draft.minimum_graph_value_over_raw_mlp ?? 0.02,
    minimum_message_passing_value_over_engineered_graph_mlp: draft.minimum_message_passing_value_over_engineered_graph_mlp ?? 0,
    pattern_heads: draft.pattern_heads || {},
    maximum_holdout_inference_ms: draft.maximum_holdout_inference_ms ?? 500,
    maximum_overall_holdout_brier_score: draft.maximum_overall_holdout_brier_score ?? 0.25,
    minimum_pattern_train_positives: draft.minimum_pattern_train_positives ?? 15,
    minimum_pattern_validation_positives: draft.minimum_pattern_validation_positives ?? 5,
    minimum_pattern_holdout_positives: draft.minimum_pattern_holdout_positives ?? 5,
    minimum_pattern_holdout_negatives: draft.minimum_pattern_holdout_negatives ?? 100,
    maximum_pattern_holdout_brier_score: draft.maximum_pattern_holdout_brier_score ?? 0.25,
    maximum_pattern_validation_pr_auc_range: draft.maximum_pattern_validation_pr_auc_range ?? 0.5,
    minimum_pattern_improvement_over_engineered_mlp: draft.minimum_pattern_improvement_over_engineered_mlp ?? 0,
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
                <label className="mt-3 block text-xs text-gray-500">Serving mode
                  <select disabled={!draft} value={policy.serving_mode} onChange={event => changeServingMode(event.target.value as GNNPolicy['serving_mode'])} className="mt-1 w-full rounded-md border border-gray-300 px-2.5 py-2 text-sm text-gray-800 disabled:bg-gray-50">
                    <option value="single_winner_v2">Single binary GNN (v2)</option>
                    <option value="scenario_specialists">Independent scenario specialists (v3)</option>
                    <option value="shared_encoder_multi_head">Shared encoder with pattern heads (v4)</option>
                  </select>
                </label>
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
                  <NumberField label="Rolling folds" value={policy.rolling_folds} disabled={!draft} min={policy.serving_mode === 'shared_encoder_multi_head' ? 3 : 2} onChange={value => edit({ rolling_folds: value })} />
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
                <p className="mt-1 text-xs text-gray-500">{policy.serving_mode === 'shared_encoder_multi_head' ? 'Architecture is selected on temporal validation. Final testing then compares the frozen winner with raw-feature and engineered-graph MLP controls.' : 'MLP remains the admission baseline. Only a graph candidate can become the live GNN architecture.'}</p>
                <div className="mt-3 flex flex-wrap gap-4">
                  {ARCHITECTURES.map(item => <label key={item.value} className="flex items-center gap-2 text-sm text-gray-700"><input type="checkbox" disabled={!draft} checked={policy.candidate_architectures.includes(item.value)} onChange={() => toggleArchitecture(item.value)} />{item.label}</label>)}
                </div>
                {policy.serving_mode !== 'shared_encoder_multi_head' && <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  <NumberField label="Minimum improvement over MLP" value={policy.minimum_improvement_over_mlp} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ minimum_improvement_over_mlp: value })} />
                  <NumberField label="Incumbent tie tolerance" value={policy.incumbent_tie_tolerance} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ incumbent_tie_tolerance: value })} />
                  <NumberField label="Minimum eligible graph candidates" value={policy.minimum_eligible_graph_candidates} disabled={!draft} min={1} max={policy.candidate_architectures.length || 1} onChange={value => edit({ minimum_eligible_graph_candidates: value })} />
                </div>}
                {policy.serving_mode === 'shared_encoder_multi_head' && <div className="mt-3 space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    <NumberField label="Overall loss weight" value={policy.overall_loss_weight ?? 1} disabled={!draft} min={0.001} step={0.1} onChange={value => edit({ overall_loss_weight: value })} />
                    <NumberField label="Pattern loss weight" value={policy.pattern_total_loss_weight ?? 1} disabled={!draft} min={0.001} step={0.1} onChange={value => edit({ pattern_total_loss_weight: value })} />
                    <NumberField label="Validation tie tolerance" value={policy.overall_tie_tolerance ?? 0.01} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ overall_tie_tolerance: value })} />
                    <NumberField label="Required value over raw MLP" value={policy.minimum_graph_value_over_raw_mlp ?? 0.02} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ minimum_graph_value_over_raw_mlp: value })} />
                    <NumberField label="Required value over engineered MLP" value={policy.minimum_message_passing_value_over_engineered_graph_mlp ?? 0} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ minimum_message_passing_value_over_engineered_graph_mlp: value })} />
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(V4_HEADS).map(([key, feature_contract]) => <label key={key} className="flex items-center gap-2 text-xs text-gray-700"><input type="checkbox" disabled={!draft} checked={policy.pattern_heads?.[key]?.enabled ?? true} onChange={event => edit({ pattern_heads: { ...policy.pattern_heads, [key]: { enabled: event.target.checked, feature_contract } } })} />{key.replaceAll('_', ' ')}</label>)}</div>
                  <details className="rounded border border-gray-200 p-3"><summary className="cursor-pointer text-xs font-semibold text-gray-700">Final-test and pattern-head admission gates</summary><div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    <NumberField label="Maximum GNN inference (ms)" value={policy.maximum_holdout_inference_ms ?? 500} disabled={!draft} min={1} onChange={value => edit({ maximum_holdout_inference_ms: value })} />
                    <NumberField label="Maximum overall Brier score" value={policy.maximum_overall_holdout_brier_score ?? 0.25} disabled={!draft} min={0} max={1} step={0.01} onChange={value => edit({ maximum_overall_holdout_brier_score: value })} />
                    <NumberField label="Pattern training positives" value={policy.minimum_pattern_train_positives ?? 15} disabled={!draft} min={1} onChange={value => edit({ minimum_pattern_train_positives: value })} />
                    <NumberField label="Pattern validation positives per period" value={policy.minimum_pattern_validation_positives ?? 5} disabled={!draft} min={1} onChange={value => edit({ minimum_pattern_validation_positives: value })} />
                    <NumberField label="Pattern final-test positives" value={policy.minimum_pattern_holdout_positives ?? 5} disabled={!draft} min={1} onChange={value => edit({ minimum_pattern_holdout_positives: value })} />
                    <NumberField label="Pattern final-test negatives" value={policy.minimum_pattern_holdout_negatives ?? 100} disabled={!draft} min={1} onChange={value => edit({ minimum_pattern_holdout_negatives: value })} />
                    <NumberField label="Maximum pattern Brier score" value={policy.maximum_pattern_holdout_brier_score ?? 0.25} disabled={!draft} min={0} max={1} step={0.01} onChange={value => edit({ maximum_pattern_holdout_brier_score: value })} />
                    <NumberField label="Maximum validation PR-AUC range" value={policy.maximum_pattern_validation_pr_auc_range ?? 0.5} disabled={!draft} min={0} max={1} step={0.01} onChange={value => edit({ maximum_pattern_validation_pr_auc_range: value })} />
                    <NumberField label="Required pattern value over engineered MLP" value={policy.minimum_pattern_improvement_over_engineered_mlp ?? 0} disabled={!draft} min={0} max={1} step={0.001} onChange={value => edit({ minimum_pattern_improvement_over_engineered_mlp: value })} />
                  </div></details>
                </div>}
                <p className="mt-3 text-xs text-gray-500">Selection metric: {policy.selection_metric.replaceAll('_', ' ')}</p>
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
