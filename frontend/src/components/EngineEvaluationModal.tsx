import React from 'react';
import { BarChart3, X } from 'lucide-react';

type JsonRecord = Record<string, unknown>;

interface Props {
  kind: 'tabular-candidates' | 'graph-value';
  data: JsonRecord;
  selectedArchitecture?: string | null;
  selectionReason?: string | null;
  onClose: () => void;
}

const asRecord = (value: unknown): JsonRecord | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : null;

const titleCase = (value: unknown): string =>
  value === null || value === undefined || value === ''
    ? '—'
    : String(value).replace(/_/g, ' ').replace(/\b\w/g, character => character.toUpperCase());

const modelName = (value: string): string => ({
  random_forest: 'Random Forest',
  tabular_mlp: 'Tabular MLP',
  mlp_tabular: 'MLP — tabular features',
  mlp_causal: 'MLP — causal features',
  graphsage: 'GraphSAGE',
  gcn: 'GCN',
  gatv2: 'GATv2',
}[value] || titleCase(value));

const percent = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toFixed(2)}%`
    : '—';

const decimal = (value: unknown, digits = 3): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
    : '—';

const integer = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? Math.round(value).toLocaleString()
    : '—';

const population = (total: unknown, positives: unknown): string => {
  if (typeof total !== 'number' || typeof positives !== 'number') return '—';
  return `${positives.toLocaleString()} fraud / ${(total - positives).toLocaleString()} legitimate`;
};

const statusClass = (eligible: boolean): string =>
  eligible
    ? 'border-green-200 bg-green-50 text-green-700'
    : 'border-amber-200 bg-amber-50 text-amber-700';

const TabularCandidateTable: React.FC<{
  evaluations: JsonRecord;
  selectedArchitecture?: string | null;
  selectionReason?: string | null;
}> = ({ evaluations, selectedArchitecture, selectionReason }) => {
  const rows = Object.entries(evaluations).map(([architecture, raw]) => {
    const candidate = asRecord(raw) || {};
    return {
      architecture,
      candidate,
      metrics: asRecord(candidate.metrics) || {},
    };
  });

  return (
    <div className="space-y-4">
      <div className="grid gap-2 text-xs sm:grid-cols-2">
        <div className="rounded-lg bg-indigo-50 p-3">
          <div className="text-indigo-500">Serving model</div>
          <div className="mt-1 font-semibold text-indigo-900">
            {selectedArchitecture ? modelName(selectedArchitecture) : '—'}
          </div>
        </div>
        <div className="rounded-lg bg-gray-50 p-3">
          <div className="text-gray-400">Selection reason</div>
          <div className="mt-1 font-medium text-gray-700">{titleCase(selectionReason)}</div>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-[1180px] w-full text-left text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-3 py-2">Candidate</th>
              <th className="px-3 py-2">Evaluation</th>
              <th className="px-3 py-2">PR-AUC</th>
              <th className="px-3 py-2">ROC-AUC</th>
              <th className="px-3 py-2">Brier score</th>
              <th className="px-3 py-2">PR-AUC lift</th>
              <th className="px-3 py-2">Training labels</th>
              <th className="px-3 py-2">Holdout labels</th>
              <th className="px-3 py-2">Features</th>
              <th className="px-3 py-2">Model size</th>
              <th className="px-3 py-2">Guardrails</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map(({ architecture, candidate, metrics }) => {
              const selected = architecture === selectedArchitecture;
              const eligible = candidate.eligible === true;
              const failures = Array.isArray(candidate.guardrail_failures)
                ? candidate.guardrail_failures.map(titleCase).join(', ')
                : '';
              const size = architecture === 'random_forest'
                ? `${integer(metrics.tree_node_count)} tree nodes`
                : `${integer(metrics.parameter_count)} parameters`;
              return (
                <tr key={architecture} className={selected ? 'bg-indigo-50/60' : ''}>
                  <td className="px-3 py-2 font-semibold text-gray-800">
                    {modelName(architecture)}
                    {selected && <span className="ml-2 text-[10px] font-medium uppercase text-indigo-600">Serving</span>}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`inline-flex rounded-full border px-2 py-0.5 font-medium ${statusClass(eligible)}`}>
                      {eligible ? (selected ? 'Selected' : 'Eligible') : titleCase(candidate.status)}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-gray-700">{percent(metrics.holdout_pr_auc)}</td>
                  <td className="px-3 py-2 font-mono text-gray-700">{percent(metrics.holdout_roc_auc)}</td>
                  <td className="px-3 py-2 font-mono text-gray-700">{decimal(metrics.holdout_brier_score, 5)}</td>
                  <td className="px-3 py-2 font-mono text-gray-700">{typeof metrics.holdout_pr_auc_lift === 'number' ? `${decimal(metrics.holdout_pr_auc_lift, 2)}×` : '—'}</td>
                  <td className="px-3 py-2 text-gray-600">{population(metrics.training_rows, metrics.training_fraud_rows)}</td>
                  <td className="px-3 py-2 text-gray-600">{population(metrics.evaluation_rows, metrics.evaluation_fraud_rows)}</td>
                  <td className="px-3 py-2 text-gray-600">{integer(metrics.feature_count)}</td>
                  <td className="px-3 py-2 text-gray-600">{size}</td>
                  <td className="px-3 py-2 text-gray-600">{failures || 'Passed'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-500">
        Both candidates use the same leakage-safe temporal holdout and the same Tabular-v1 feature contract.
      </p>
    </div>
  );
};

const GraphValueTable: React.FC<{ evaluation: JsonRecord }> = ({ evaluation }) => {
  const ablation = asRecord(evaluation.ablation) || {};
  const candidates = asRecord(evaluation.candidates) || {};
  const rows = Object.entries(candidates).map(([name, raw]) => ({
    name,
    candidate: asRecord(raw) || {},
  }));

  return (
    <div className="space-y-4">
      <div className="grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-5">
        <div className="rounded-lg bg-gray-50 p-3"><div className="text-gray-400">Tabular-feature MLP</div><div className="mt-1 font-semibold text-gray-800">{percent(ablation.mlp_tabular_value)}</div></div>
        <div className="rounded-lg bg-gray-50 p-3"><div className="text-gray-400">Causal-feature MLP</div><div className="mt-1 font-semibold text-gray-800">{percent(ablation.mlp_causal_value)}</div></div>
        <div className="rounded-lg bg-green-50 p-3"><div className="text-green-600">Causal feature gain</div><div className="mt-1 font-semibold text-green-800">{percent(ablation.engineered_causal_feature_gain)}</div></div>
        <div className="rounded-lg bg-indigo-50 p-3"><div className="text-indigo-500">Best graph profile</div><div className="mt-1 font-semibold text-indigo-900">{ablation.best_graph_architecture ? modelName(String(ablation.best_graph_architecture)) : '—'} · {percent(ablation.best_graph_value)}</div></div>
        <div className="rounded-lg bg-violet-50 p-3"><div className="text-violet-500">Message-passing gain</div><div className="mt-1 font-semibold text-violet-900">{percent(ablation.graph_message_passing_gain)}</div></div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-[900px] w-full text-left text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-3 py-2">Candidate</th>
              <th className="px-3 py-2">Feature profile</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Macro PR-AUC</th>
              <th className="px-3 py-2">Lowest fold</th>
              <th className="px-3 py-2">Highest fold</th>
              <th className="px-3 py-2">Rolling folds</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map(({ name, candidate }) => {
              const best = name === ablation.best_graph_architecture;
              return (
                <tr key={name} className={best ? 'bg-indigo-50/60' : ''}>
                  <td className="px-3 py-2 font-semibold text-gray-800">
                    {modelName(name)}
                    {best && <span className="ml-2 text-[10px] font-medium uppercase text-indigo-600">Best graph</span>}
                  </td>
                  <td className="px-3 py-2 text-gray-600">{titleCase(candidate.feature_profile)}</td>
                  <td className="px-3 py-2 text-gray-600">{titleCase(candidate.status)}</td>
                  <td className="px-3 py-2 font-mono text-gray-700">{percent(candidate.macro_pr_auc)}</td>
                  <td className="px-3 py-2 font-mono text-gray-700">{percent(candidate.minimum_fold_pr_auc)}</td>
                  <td className="px-3 py-2 font-mono text-gray-700">{percent(candidate.maximum_fold_pr_auc)}</td>
                  <td className="px-3 py-2 text-gray-600">{integer(candidate.fold_count)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-500">
        This is diagnostic evidence from leakage-safe rolling-origin evaluation. It does not promote or reject the serving GNN.
      </p>
    </div>
  );
};

export const EngineEvaluationModal: React.FC<Props> = ({
  kind,
  data,
  selectedArchitecture,
  selectionReason,
  onClose,
}) => {
  const tabular = kind === 'tabular-candidates';
  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/40 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label={tabular ? 'Candidate Evaluation' : 'Graph Value Evaluation'}>
      <div className="mx-auto max-w-[1400px] overflow-hidden rounded-xl bg-white shadow-2xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <h3 className="flex items-center gap-2 text-lg font-semibold text-gray-900">
              <BarChart3 className="h-5 w-5 text-indigo-600" />
              {tabular ? 'Candidate Evaluation' : 'Graph Value Evaluation'}
            </h3>
            <p className="mt-1 text-xs text-gray-500">
              {tabular
                ? 'Random Forest and Tabular MLP evaluated on the same out-of-time holdout.'
                : 'Feature-profile and graph-architecture comparison from the latest GNN run.'}
            </p>
          </div>
          <button type="button" onClick={onClose} className="rounded p-2 text-gray-500 hover:bg-gray-100" title="Close" aria-label="Close evaluation">
            <X className="h-5 w-5" />
          </button>
        </header>
        <div className="p-4 sm:p-6">
          {tabular ? (
            <TabularCandidateTable
              evaluations={data}
              selectedArchitecture={selectedArchitecture}
              selectionReason={selectionReason}
            />
          ) : (
            <GraphValueTable evaluation={data} />
          )}
        </div>
      </div>
    </div>
  );
};
