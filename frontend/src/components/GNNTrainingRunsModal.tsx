import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle, BrainCircuit, Clock3, FileJson,
  RefreshCw, ShieldCheck, X,
} from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type JsonRecord = Record<string, unknown>;

interface ServingSummary {
  status: string;
  version: string | null;
  as_of: string | null;
  last_successful_at: string | null;
}

interface GNNTrainingRun {
  serving_status: string;
  serving_version: string | null;
  serving_as_of: string | null;
  last_attempt_status: string;
  reason_code: string | null;
  reason_detail: string | null;
  diagnostics: JsonRecord;
  last_attempt_at: string | null;
  run_id: string;
  model_version: string | null;
  selected_architecture: string | null;
  selection_reason: string | null;
  artifact_bundle_prefix: string | null;
  is_current: boolean;
}

interface RunListResponse {
  serving: ServingSummary | null;
  runs: GNNTrainingRun[];
}

interface ManifestArtifact {
  file_name: string;
  relative_path?: string;
  role: string;
  size_bytes: number;
  sha256: string;
}

interface GNNManifest {
  schema_version: number;
  tenant_id: number;
  model_version: string;
  generated_at: string;
  description: string;
  graph_snapshot_id?: string;
  graph_snapshot_as_of?: string;
  selection?: JsonRecord;
  training_policy?: JsonRecord;
  artifacts?: ManifestArtifact[];
}

interface RunDetailResponse {
  available: boolean;
  run: GNNTrainingRun;
  manifest: GNNManifest | null;
  message?: string | null;
}

interface Props {
  impersonatedUPN?: string;
  onClose: () => void;
}

const asRecord = (value: unknown): JsonRecord | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : null;

const latestSelection = (run: GNNTrainingRun | null): JsonRecord | null => {
  if (!run) return null;
  return asRecord(run.diagnostics.last_candidate_selection)
    || asRecord(run.diagnostics.selection);
};

const label = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '—';
  return String(value).replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase());
};

const dateTime = (value: string | null | undefined): string => {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
};

const decimal = (value: unknown, digits = 4): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
    : '—';

const percent = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(2)}%`
    : '—';

const integer = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? Math.round(value).toLocaleString()
    : '—';

const duration = (value: unknown, unit: string): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`
    : '—';

const labelPopulation = (total: unknown, positives: unknown): string => {
  if (typeof total !== 'number' || typeof positives !== 'number') return '—';
  return `${positives.toLocaleString()} fraud / ${(total - positives).toLocaleString()} legitimate`;
};

const badgeClass = (status: string | null | undefined): string => {
  switch ((status || '').toUpperCase()) {
    case 'AVAILABLE':
    case 'SUCCEEDED':
    case 'COMPLETED':
      return 'border-green-200 bg-green-50 text-green-700';
    case 'SKIPPED':
    case 'STALE':
      return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'FAILED':
    case 'UNAVAILABLE':
      return 'border-red-200 bg-red-50 text-red-700';
    default:
      return 'border-gray-200 bg-gray-50 text-gray-600';
  }
};

const bestGraphCandidate = (selection: JsonRecord | null): [string, JsonRecord] | null => {
  const candidates = asRecord(selection?.candidates) || {};
  const graphs = Object.entries(candidates)
    .filter(([name, value]) => name !== 'mlp' && typeof asRecord(value)?.eval_pr_auc === 'number')
    .map(([name, value]) => [name, asRecord(value) as JsonRecord] as [string, JsonRecord]);
  return graphs.sort((left, right) => Number(right[1].eval_pr_auc) - Number(left[1].eval_pr_auc))[0] || null;
};

const CandidateComparison: React.FC<{ selection: JsonRecord }> = ({ selection }) => {
  const candidates = asRecord(selection.candidates) || {};
  const mlp = asRecord(candidates.mlp);
  const baseline = typeof selection.mlp_baseline_value === 'number'
    ? selection.mlp_baseline_value
    : mlp?.eval_pr_auc;
  const minimumImprovement = typeof selection.minimum_improvement_over_mlp === 'number'
    ? selection.minimum_improvement_over_mlp
    : 0;
  const admissionThreshold = typeof baseline === 'number'
    ? baseline + minimumImprovement
    : null;

  return (
    <section className="space-y-3">
      <div>
        <h4 className="font-semibold text-gray-800">Candidate comparison</h4>
        <p className="mt-1 text-xs text-gray-500">
          MLP is the non-serving admission baseline. A graph architecture must be eligible and meet the recorded PR-AUC admission threshold.
        </p>
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-[1500px] w-full text-left text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-3 py-2">Model</th>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Evaluation</th>
              <th className="px-3 py-2">PR-AUC</th>
              <th className="px-3 py-2">Δ vs MLP</th>
              <th className="px-3 py-2">Lift</th>
              <th className="px-3 py-2">ROC-AUC</th>
              <th className="px-3 py-2">Brier</th>
              <th className="px-3 py-2">Training labels</th>
              <th className="px-3 py-2">Holdout labels</th>
              <th className="px-3 py-2">Epochs</th>
              <th className="px-3 py-2">Training</th>
              <th className="px-3 py-2">Inference</th>
              <th className="px-3 py-2">Parameters</th>
              <th className="px-3 py-2">Guardrails</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {Object.entries(candidates).map(([name, raw]) => {
              const candidate = asRecord(raw) || {};
              const isBaseline = name === 'mlp';
              const prAuc = typeof candidate.eval_pr_auc === 'number' ? candidate.eval_pr_auc : null;
              const delta = prAuc !== null && typeof baseline === 'number' ? prAuc - baseline : null;
              const eligible = candidate.eligible === true;
              const admitted = !isBaseline && eligible && prAuc !== null
                && admissionThreshold !== null && prAuc >= admissionThreshold;
              const selected = name === selection.selected_architecture;
              const failures = Array.isArray(candidate.guardrail_failures)
                ? candidate.guardrail_failures.map(label).join(', ')
                : '';
              const evaluation = isBaseline
                ? 'Baseline'
                : selected
                  ? 'Selected'
                  : !eligible
                    ? 'Ineligible'
                    : admitted
                      ? 'Admitted'
                      : 'Not admitted';
              return (
                <tr key={name} className={selected ? 'bg-indigo-50/60' : ''}>
                  <td className="px-3 py-2 font-semibold text-gray-800">{isBaseline ? 'MLP' : name.toUpperCase()}</td>
                  <td className="px-3 py-2 text-gray-600">{isBaseline ? 'Admission baseline' : 'Graph candidate'}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-flex rounded-full border px-2 py-0.5 font-medium ${isBaseline ? badgeClass('BASELINE') : selected || admitted ? badgeClass('SUCCEEDED') : badgeClass(eligible ? 'SKIPPED' : 'FAILED')}`}>
                      {evaluation}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-gray-700">{percent(prAuc)}</td>
                  <td className={`px-3 py-2 font-mono ${typeof delta === 'number' && delta >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                    {typeof delta === 'number' ? `${delta >= 0 ? '+' : ''}${percent(delta)}` : '—'}
                  </td>
                  <td className="px-3 py-2 font-mono text-gray-600">{typeof candidate.eval_lift === 'number' ? `${decimal(candidate.eval_lift, 2)}×` : '—'}</td>
                  <td className="px-3 py-2 font-mono text-gray-600">{percent(candidate.eval_roc_auc)}</td>
                  <td className="px-3 py-2 font-mono text-gray-600">{decimal(candidate.eval_brier_score, 5)}</td>
                  <td className="px-3 py-2 text-gray-600">{labelPopulation(candidate.n_train, candidate.n_train_pos)}</td>
                  <td className="px-3 py-2 text-gray-600">{labelPopulation(candidate.n_eval, candidate.n_eval_pos)}</td>
                  <td className="px-3 py-2 font-mono text-gray-600">{integer(candidate.epochs_run)}</td>
                  <td className="px-3 py-2 font-mono text-gray-600">{duration(candidate.training_duration_seconds, 's')}</td>
                  <td className="px-3 py-2 font-mono text-gray-600">{duration(candidate.holdout_inference_ms, 'ms')}</td>
                  <td className="px-3 py-2 font-mono text-gray-600">{integer(candidate.parameter_count)}</td>
                  <td className="px-3 py-2 text-gray-600">{failures || 'Passed'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="grid gap-2 text-xs sm:grid-cols-3">
        <div className="rounded bg-gray-50 p-2"><span className="text-gray-400">MLP baseline</span><div className="font-medium text-gray-700">{percent(baseline)}</div></div>
        <div className="rounded bg-gray-50 p-2"><span className="text-gray-400">Required improvement</span><div className="font-medium text-gray-700">{percent(minimumImprovement)}</div></div>
        <div className="rounded bg-gray-50 p-2"><span className="text-gray-400">Graph admission threshold</span><div className="font-medium text-gray-700">{percent(admissionThreshold)}</div></div>
      </div>
      <details className="rounded-lg border border-gray-200 bg-gray-50">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-gray-600">Raw candidate metrics</summary>
        <pre className="max-h-80 overflow-auto border-t border-gray-200 p-3 text-[11px] text-gray-600">{JSON.stringify(candidates, null, 2)}</pre>
      </details>
    </section>
  );
};

export const GNNTrainingRunsModal: React.FC<Props> = ({ impersonatedUPN, onClose }) => {
  const [response, setResponse] = useState<RunListResponse | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const headers = useCallback(async () => {
    const token = await getAccessToken();
    const result: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (impersonatedUPN) result['X-Impersonate-User'] = impersonatedUPN;
    return result;
  }, [impersonatedUPN]);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/model-analysis/setup/models/gnn/runs?limit=50`, { headers: await headers() });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const body = await res.json() as RunListResponse;
      setResponse(body);
      setSelectedRunId(current => current && body.runs.some(run => run.run_id === current)
        ? current
        : body.runs[0]?.run_id || null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to load GNN training runs');
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => { void loadRuns(); }, [loadRuns]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedRunId) {
      setDetail(null);
      return () => { cancelled = true; };
    }
    const loadDetail = async () => {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/model-analysis/setup/models/gnn/runs/${encodeURIComponent(selectedRunId)}`,
          { headers: await headers() },
        );
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
        const body = await res.json() as RunDetailResponse;
        if (!cancelled) setDetail(body);
      } catch (caught) {
        if (!cancelled) setDetailError(caught instanceof Error ? caught.message : 'Failed to load the run manifest');
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    };
    void loadDetail();
    return () => { cancelled = true; };
  }, [headers, selectedRunId]);

  const selectedRun = useMemo(
    () => response?.runs.find(run => run.run_id === selectedRunId) || null,
    [response, selectedRunId],
  );
  const diagnosticsSelection = latestSelection(detail?.run || selectedRun);
  const selection = asRecord(detail?.manifest?.selection) || diagnosticsSelection;
  const artifacts = detail?.manifest?.artifacts || [];

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label="GNN training runs">
      <div className="mx-auto max-w-[1500px] overflow-hidden rounded-xl bg-white shadow-2xl">
        <header className="sticky top-0 z-20 flex items-start justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <h3 className="flex items-center gap-2 text-lg font-semibold text-gray-900"><BrainCircuit className="h-5 w-5 text-violet-600" />GNN Training Runs</h3>
            <p className="mt-0.5 text-xs text-gray-500">Temporal attempt history with immutable candidate manifests</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={loadRuns} disabled={loading} className="rounded p-2 text-gray-500 hover:bg-gray-100 disabled:opacity-40" title="Refresh training runs"><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /></button>
            <button onClick={onClose} className="rounded p-2 text-gray-500 hover:bg-gray-100" title="Close training runs" aria-label="Close training runs"><X className="h-5 w-5" /></button>
          </div>
        </header>

        <div className="space-y-5 p-4 sm:p-6">
          {response?.serving && (
            <section className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h4 className="flex items-center gap-2 text-sm font-semibold text-gray-800"><ShieldCheck className="h-4 w-4 text-indigo-600" />Serving model</h4>
                  <p className="mt-1 text-xs text-gray-500">The currently activated GNN, independent from the most recent training outcome.</p>
                </div>
                <span className={`rounded-full border px-2.5 py-1 text-xs font-medium ${badgeClass(response.serving.status)}`}>{response.serving.status}</span>
              </div>
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
                <div className="rounded bg-white p-2"><dt className="text-gray-400">Version</dt><dd className="mt-0.5 break-all font-mono text-gray-700">{response.serving.version || '—'}</dd></div>
                <div className="rounded bg-white p-2"><dt className="text-gray-400">Serving as of</dt><dd className="mt-0.5 text-gray-700">{dateTime(response.serving.as_of)}</dd></div>
                <div className="rounded bg-white p-2"><dt className="text-gray-400">Last successful training</dt><dd className="mt-0.5 text-gray-700">{dateTime(response.serving.last_successful_at)}</dd></div>
              </dl>
            </section>
          )}

          {loading && <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-400"><RefreshCw className="h-4 w-4 animate-spin" />Loading training runs…</div>}
          {!loading && error && <div className="flex items-start gap-2 rounded-lg bg-red-50 p-4 text-sm text-red-700"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
          {!loading && !error && response?.runs.length === 0 && <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center text-sm text-gray-500">No GNN training attempts have been recorded.</div>}

          {!loading && !error && response && response.runs.length > 0 && (
            <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
              <aside className="space-y-2">
                <h4 className="text-sm font-semibold text-gray-700">Training-run history</h4>
                <div className="max-h-[72vh] space-y-2 overflow-y-auto pr-1">
                  {response.runs.map(run => {
                    const runSelection = latestSelection(run);
                    const best = bestGraphCandidate(runSelection);
                    const active = run.run_id === selectedRunId;
                    return (
                      <button
                        key={run.run_id}
                        type="button"
                        onClick={() => setSelectedRunId(run.run_id)}
                        className={`w-full rounded-lg border p-3 text-left transition ${active ? 'border-violet-300 bg-violet-50 shadow-sm' : 'border-gray-200 bg-white hover:border-gray-300'}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${badgeClass(run.last_attempt_status)}`}>{run.last_attempt_status}</span>
                          {run.is_current && <span className="text-[10px] font-medium uppercase tracking-wide text-violet-600">Latest</span>}
                        </div>
                        <div className="mt-2 flex items-center gap-1 text-xs text-gray-500"><Clock3 className="h-3.5 w-3.5" />{dateTime(run.last_attempt_at)}</div>
                        <div className="mt-2 break-all font-mono text-[11px] text-gray-700">{run.model_version || run.run_id}</div>
                        <div className="mt-2 text-xs text-gray-600">{run.selected_architecture ? `Selected ${run.selected_architecture.toUpperCase()}` : label(run.selection_reason || run.reason_code)}</div>
                        {best && <div className="mt-1 text-[11px] text-gray-400">Best graph: {best[0].toUpperCase()} · {percent(best[1].eval_pr_auc)}</div>}
                      </button>
                    );
                  })}
                </div>
              </aside>

              <main className="min-w-0 space-y-5">
                {detailLoading && <div className="flex items-center justify-center gap-2 py-20 text-sm text-gray-400"><RefreshCw className="h-4 w-4 animate-spin" />Loading immutable manifest…</div>}
                {!detailLoading && detailError && <div className="flex items-start gap-2 rounded-lg bg-red-50 p-4 text-sm text-red-700"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{detailError}</div>}
                {!detailLoading && !detailError && selectedRun && (
                  <>
                    <section className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <h4 className="font-semibold text-gray-800">{selectedRun.is_current ? 'Latest training attempt' : 'Historical training attempt'}</h4>
                          <p className="mt-1 break-all font-mono text-xs text-gray-500">{selectedRun.model_version || selectedRun.run_id}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          {detail?.available && <span className="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700"><FileJson className="h-3.5 w-3.5" />Immutable manifest</span>}
                          <span className={`rounded-full border px-2 py-1 text-xs font-medium ${badgeClass(selectedRun.last_attempt_status)}`}>{selectedRun.last_attempt_status}</span>
                        </div>
                      </div>
                      <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Attempted</dt><dd className="font-medium text-gray-700">{dateTime(selectedRun.last_attempt_at)}</dd></div>
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Run ID</dt><dd className="break-all font-mono text-gray-700">{selectedRun.run_id}</dd></div>
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Selection reason</dt><dd className="font-medium text-gray-700">{label(selection?.selection_reason || selectedRun.reason_code)}</dd></div>
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Selected architecture</dt><dd className="font-medium text-gray-700">{selection?.selected_architecture ? String(selection.selected_architecture).toUpperCase() : 'None'}</dd></div>
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Graph snapshot as of</dt><dd className="font-medium text-gray-700">{String(detail?.manifest?.graph_snapshot_as_of || selectedRun.diagnostics.graph_snapshot_as_of || '—')}</dd></div>
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Policy version</dt><dd className="font-medium text-gray-700">{String(detail?.manifest?.training_policy?.policy_version ?? selectedRun.diagnostics.gnn_policy_version ?? '—')}</dd></div>
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Training targets</dt><dd className="font-medium text-gray-700">{integer(selectedRun.diagnostics.supervised_train_count)}</dd></div>
                        <div className="rounded bg-white p-2"><dt className="text-gray-400">Holdout targets</dt><dd className="font-medium text-gray-700">{integer(selectedRun.diagnostics.supervised_eval_count)}</dd></div>
                      </dl>
                      {(selectedRun.reason_detail || detail?.message) && <p className="mt-3 text-xs text-gray-600">{selectedRun.reason_detail || detail?.message}</p>}
                    </section>

                    {selection ? <CandidateComparison selection={selection} /> : (
                      <div className="rounded-lg border border-dashed border-gray-200 py-12 text-center text-sm text-gray-500">This attempt ended before candidate evaluation.</div>
                    )}

                    {artifacts.length > 0 && (
                      <details className="rounded-lg border border-gray-200">
                        <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-gray-700">Immutable run artifacts ({artifacts.length})</summary>
                        <div className="divide-y divide-gray-100 border-t border-gray-200">
                          {artifacts.map(artifact => (
                            <div key={`${artifact.role}-${artifact.relative_path || artifact.file_name}`} className="grid gap-1 px-4 py-3 text-xs sm:grid-cols-[minmax(0,1fr)_180px]">
                              <div><div className="font-medium text-gray-700">{label(artifact.role)}</div><div className="mt-0.5 break-all font-mono text-gray-400">{artifact.relative_path || artifact.file_name}</div></div>
                              <div className="break-all font-mono text-[10px] text-gray-400" title={artifact.sha256}>SHA-256 {artifact.sha256}</div>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </>
                )}
              </main>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
