import React from 'react';

export const GRAPH_PATTERN_LABELS: Record<string, string> = {
  Ring: 'Nomination Ring',
  BipartiteDenseBlock: 'Bipartite Dense Block',
  TemporalBurst: 'Temporal Burst',
  SuperNominator: 'Super Nominator',
  SuperBeneficiary: 'Super Beneficiary',
  CopyPaste: 'Copy-Paste Fraud',
  HiddenCandidate: 'Hidden Candidate',
  Desert: 'Nomination Desert',
  LowRecognitionNominator: 'Frequent nominator, seldom nominated',
};

export interface GraphDetectorScore {
  detector: string;
  score: number;
  severity?: string;
  eligible: boolean;
  enabled_for_routing: boolean;
  state: 'SCORING' | 'NOT_SCORING' | 'ANALYTICS_ONLY';
  eligibility_reasons?: string[];
  detail?: string;
}

/** The logs API exposes parsed JSON as unknown; ignore malformed score rows. */
export function parseGraphDetectorScores(value: unknown): GraphDetectorScore[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is GraphDetectorScore =>
    item !== null && typeof item === 'object'
    && typeof item.detector === 'string'
    && typeof item.score === 'number'
    && Number.isFinite(item.score)
  );
}

/** Shared Graph Analytics detector summary for review and nomination logs. */
export const OtherGraphDetectorScores: React.FC<{
  scores: GraphDetectorScore[];
  winningPatternType?: string | null;
}> = ({ scores, winningPatternType }) => {
  const otherScores = scores
    .filter(item => item.detector !== winningPatternType)
    .sort((left, right) => right.score - left.score || left.detector.localeCompare(right.detector));
  if (otherScores.length === 0) return null;

  return (
    <div className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">
      <p className="font-semibold text-slate-900">Other detector scores</p>
      <p className="text-slate-500">Current nomination evaluation · scores are not summed.</p>
      <div className="mt-2 space-y-1.5">
        {otherScores.map(item => {
          const statusLabel = item.state === 'SCORING'
            ? 'Eligible'
            : item.state === 'ANALYTICS_ONLY'
              ? 'Analytics only'
              : 'Not eligible';
          const explanation = item.state === 'SCORING'
            ? 'Participated; lower than the winning detector.'
            : item.state === 'ANALYTICS_ONLY'
              ? 'Excluded from routing by policy.'
              : item.eligibility_reasons?.[0] || 'Minimum detector criteria were not met.';
          return (
            <div
              key={item.detector}
              className={`rounded-md border px-2 py-1.5 ${
                item.state === 'SCORING'
                  ? 'border-emerald-200 bg-emerald-50/60'
                  : item.state === 'ANALYTICS_ONLY'
                    ? 'border-slate-200 bg-slate-100'
                    : 'border-amber-200 bg-amber-50/60'
              }`}
              title={(item.eligibility_reasons || []).join('; ') || item.detail}
            >
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium text-slate-800">
                  {GRAPH_PATTERN_LABELS[item.detector] || `${item.detector} pattern`}
                </span>
                <span className="flex-shrink-0 rounded bg-white px-1.5 py-0.5 font-semibold text-indigo-700 shadow-sm">
                  {item.score.toFixed(2)}
                </span>
              </div>
              <p className={`mt-1 leading-tight ${
                item.state === 'SCORING'
                  ? 'text-emerald-700'
                  : item.state === 'ANALYTICS_ONLY'
                    ? 'text-slate-500'
                    : 'text-amber-700'
              }`}>
                <span className="font-semibold">{statusLabel}</span> · {explanation}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
};
