import React from 'react';

type JsonRecord = Record<string, unknown>;

const asRecord = (value: unknown): JsonRecord | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : null;

const count = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? Math.round(value).toLocaleString()
    : '—';

const dateValue = (value: unknown): string => {
  if (typeof value !== 'string' || !value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
};

const gateLabels: Record<string, string> = {
  training_positive_labels: 'Training pattern-positive labels',
  training_negative_labels: 'Training legitimate labels',
  final_holdout_positive_labels: 'Final holdout pattern-positive labels',
  final_holdout_negative_labels: 'Final holdout legitimate labels',
  evaluable_selection_folds: 'Evaluable selection folds',
};

const populationCount = (population: JsonRecord | null, key: 'positive_count' | 'negative_count'): string =>
  count(population?.[key]);

export const SpecialistLabelEvidence: React.FC<{ evidence: unknown }> = ({ evidence }) => {
  const value = asRecord(evidence);
  if (!value) return null;

  const checks = asRecord(value.checks) || {};
  const folds = (Array.isArray(value.folds) ? value.folds : [])
    .map(asRecord)
    .filter((fold): fold is JsonRecord => fold !== null);

  return (
    <section className="mt-3 space-y-3 rounded-lg border border-amber-200 bg-amber-50/40 p-3">
      <div>
        <h6 className="text-xs font-semibold text-gray-800">Specialist label admission evidence</h6>
        <p className="mt-0.5 text-[11px] text-gray-500">Pattern-positive means this specific behavior was confirmed; other fraud types are excluded rather than counted as negatives. Fold rows show exactly where the labels occur.</p>
      </div>

      <div className="grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-5">
        {Object.entries(gateLabels).map(([key, title]) => {
          const gate = asRecord(checks[key]);
          if (!gate) return null;
          const actual = typeof gate.actual === 'number' ? gate.actual : null;
          const required = typeof gate.required === 'number' ? gate.required : null;
          const passed = gate.passed === true;
          const shortfall = actual !== null && required !== null ? Math.max(0, required - actual) : null;
          return (
            <div key={key} className={`rounded border p-2 ${passed ? 'border-green-200 bg-green-50' : 'border-amber-200 bg-white'}`}>
              <div className="text-gray-500">{title}</div>
              <div className="mt-1 font-semibold text-gray-800">{count(actual)} of {count(required)} required</div>
              <div className={`mt-0.5 text-[10px] font-medium ${passed ? 'text-green-700' : 'text-amber-700'}`}>
                {passed ? 'Requirement met' : shortfall === null ? 'Requirement not met' : `Short by ${count(shortfall)}`}
              </div>
            </div>
          );
        })}
      </div>

      {folds.length > 0 && (
        <div className="overflow-x-auto rounded border border-gray-200 bg-white">
          <table className="w-full min-w-[760px] text-left text-xs">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="px-3 py-2">Fold</th>
                <th className="px-3 py-2">Purpose</th>
                <th className="px-3 py-2 text-right">Training pattern-positive</th>
                <th className="px-3 py-2 text-right">Training legitimate</th>
                <th className="px-3 py-2 text-right">Evaluation pattern-positive</th>
                <th className="px-3 py-2 text-right">Evaluation legitimate</th>
                <th className="px-3 py-2">Both classes</th>
                <th className="px-3 py-2">Evaluation end</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {folds.map((fold, index) => {
                const training = asRecord(fold.training);
                const evaluation = asRecord(fold.evaluation);
                const hasBothClasses = fold.evaluation_has_both_classes === true;
                return (
                  <tr key={`${String(fold.fold_index ?? index + 1)}-${String(fold.role ?? '')}`}>
                    <td className="px-3 py-2 font-medium text-gray-700">Fold {count(fold.fold_index ?? index + 1)}</td>
                    <td className="px-3 py-2 text-gray-600">{fold.role === 'FINAL_HOLDOUT' ? 'Final holdout' : 'Selection'}</td>
                    <td className="px-3 py-2 text-right font-mono text-gray-700">{populationCount(training, 'positive_count')}</td>
                    <td className="px-3 py-2 text-right font-mono text-gray-700">{populationCount(training, 'negative_count')}</td>
                    <td className="px-3 py-2 text-right font-mono text-gray-700">{populationCount(evaluation, 'positive_count')}</td>
                    <td className="px-3 py-2 text-right font-mono text-gray-700">{populationCount(evaluation, 'negative_count')}</td>
                    <td className={`px-3 py-2 font-medium ${hasBothClasses ? 'text-green-700' : 'text-amber-700'}`}>{hasBothClasses ? 'Yes' : 'No'}</td>
                    <td className="px-3 py-2 text-gray-600">{dateValue(fold.evaluation_end)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};
