/**
 * HRBPReviewTab.tsx
 * -----------------
 * Displays the HRBP fraud review queue for the current tenant.
 *
 * For each nomination in PendingHRBPReview the HRBP reviewer can see:
 *   - Nominator → Beneficiary, amount, category, description
 *   - The complete RF, Graph, GNN, and semantic engine verdict
 *   - All other nominations between these two people in either direction (expandable)
 *   - Scope-aware human outcomes with an explicit training disposition
 */

import React, { useState, useEffect, useCallback } from 'react';
import { ShieldAlert, ChevronDown, ChevronUp, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { useImpersonation } from '../contexts/ImpersonationContext';
import { SHAP_FEATURE_LABELS, type ShapContribution } from '../utils/shap';

// ── Types ──────────────────────────────────────────────────────────────────

export interface HRBPQueueItem {
  nomination_id:      number;
  status:             string;
  amount:             number;
  currency:           string;
  description:        string;
  nomination_date:    string;
  nominator_name:     string;
  nominator_email:    string;
  beneficiary_name:   string;
  beneficiary_email:  string;
  fraud_score:        number | null;
  fraud_probability:  number | null;
  risk_level:         string | null;
  warning_flags:      string[];
  top_features:       string | null;
  feature_summary:    string | null;
  llm_explanation:    string | null;
  decision_source:    'integrity_v2' | 'legacy' | null;
  decision_schema_version: number | null;
  review_scope:       'FRAUD' | 'SEMANTIC' | 'FRAUD_AND_SEMANTIC' | 'LEGACY_FRAUD' | null;
  decisive_engines:   string[];
  engine_results:     Record<string, EngineResult | null>;
  final_route:        string | null;
  routing_rule:       string | null;
}

export interface EngineResult {
  engine?: string;
  available?: boolean;
  status?: string;
  unavailable_reason?: string | null;
  unavailable_detail?: string | null;
  score?: number | null;
  model_probability?: number | null;
  risk_level?: string;
  findings?: string[];
  combined_decision?: {
    action?: string;
    checks?: string[];
    reason?: string | null;
  };
  embedding?: { similarity?: number; threshold?: number; outcome?: string };
  llm?: { response?: { category_fit_score?: number; flags?: string[] }; outcome?: string };
}

export interface PairHistoryItem {
  nomination_id:    number;
  amount:           number;
  currency:         string;
  description:      string;
  nomination_date:  string;
  status:           string;
  risk_level:       string | null;
  nominator_name:   string;
  beneficiary_name: string;
}

export interface PairHistory {
  nominator_name:   string;
  beneficiary_name: string;
  pair_count:       number;
  history:          PairHistoryItem[];
}

type HRBPOutcome =
  | 'CLEARED_NO_CONCERN'
  | 'CLEARED_UNSUBSTANTIATED'
  | 'CONFIRMED_CONCERN'
  | 'CONFIRMED_SEMANTIC_CONCERN';

// ── SHAP feature label map ────────────────────────────────────────────────

// ── ShapPanel component ───────────────────────────────────────────────────

export const ShapPanel: React.FC<{ topFeaturesJson: string | null }> = ({ topFeaturesJson }) => {
  if (!topFeaturesJson) return null;

  let contributions: ShapContribution[] = [];
  try {
    contributions = JSON.parse(topFeaturesJson);
  } catch {
    return null;
  }
  if (!contributions.length) return null;

  const maxAbs = Math.max(...contributions.map(c => Math.abs(c.contribution)), 0.001);

  return (
    <div className="mt-3 mb-3 bg-slate-50 border border-slate-200 rounded-lg p-4">
      <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-3">
        RF model (SHAP) breakdown (top contributing factors)
      </p>
      <div className="space-y-2">
        {contributions.map((c, i) => {
          const pct     = Math.round((Math.abs(c.contribution) / maxAbs) * 100);
          const isRisk  = c.contribution > 0;
          const barColour = isRisk ? 'bg-orange-400' : 'bg-emerald-400';
          const label   = SHAP_FEATURE_LABELS[c.feature] ?? c.feature;
          const sign    = isRisk ? '+' : '';

          return (
            <div key={i} className="flex items-center gap-3">
              {/* Label + value */}
              <div className="w-56 flex-shrink-0">
                <p className="text-xs text-slate-700 leading-tight">{label}</p>
                <p className="text-xs text-slate-400">{c.raw_value}</p>
              </div>
              {/* Bar */}
              <div className="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden">
                <div
                  className={`h-2 rounded-full ${barColour}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {/* Contribution in percentage points */}
              <span className={`text-xs font-mono w-16 text-right flex-shrink-0 ${isRisk ? 'text-orange-600' : 'text-emerald-600'}`}>
                {sign}{(c.contribution * 100).toFixed(1)} pp
              </span>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-slate-400 mt-3">
        pp = percentage points of fraud probability · Orange pushes up · Green pushes down · Width = relative strength
      </p>
    </div>
  );
};

// ── Helpers ────────────────────────────────────────────────────────────────

const RISK_COLOURS: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-800 border-red-300',
  HIGH:     'bg-orange-100 text-orange-800 border-orange-300',
  MEDIUM:   'bg-yellow-100 text-yellow-800 border-yellow-300',
  LOW:      'bg-blue-100 text-blue-800 border-blue-300',
  NONE:     'bg-green-100 text-green-800 border-green-300',
  UNKNOWN:  'bg-gray-100 text-gray-700 border-gray-300',
};

export const RiskBadge: React.FC<{ level: string | null }> = ({ level }) => {
  const key = (level || 'UNKNOWN').toUpperCase();
  return (
    <span className={`inline-block px-2 py-0.5 rounded border text-xs font-semibold ${RISK_COLOURS[key] ?? RISK_COLOURS.UNKNOWN}`}>
      {key}
    </span>
  );
};

export const EngineVerdicts: React.FC<{ item: HRBPQueueItem }> = ({ item }) => {
  if (item.decision_source === 'legacy') {
    return (
      <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        Legacy decision record — a complete four-engine evidence snapshot was not captured.
      </div>
    );
  }

  const entries = [
    ['RF', item.engine_results.rf],
    ['Graph Analytics', item.engine_results.graph],
    ['GNN', item.engine_results.gnn],
    ['Semantic', item.engine_results.semantic],
  ] as const;

  return (
    <div className="mb-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">
          Detection engine verdicts
        </p>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
          Review scope: {item.review_scope?.replace(/_/g, ' ') || (
            item.final_route && item.final_route !== 'HRBP_REVIEW'
              && item.status !== 'PendingHRBPReview'
              ? 'Not applicable'
              : 'Unavailable'
          )}
        </span>
        {item.decisive_engines.length > 0 && (
          <span className="rounded bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">
            Decisive: {item.decisive_engines.join(', ')}
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {entries.map(([label, engine]) => {
          if (!engine) return null;
          const isSemantic = label === 'Semantic';
          const action = engine.combined_decision?.action;
          const findings = [
            ...(engine.findings || []),
            ...(engine.combined_decision?.checks || []),
          ];
          return (
            <div key={label} className="rounded-lg border border-slate-200 bg-white p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-800">{label}</p>
                {isSemantic
                  ? <span className="text-xs font-semibold uppercase text-slate-600">{action || engine.status}</span>
                  : <RiskBadge level={engine.risk_level || 'UNKNOWN'} />}
              </div>
              {!isSemantic && engine.available && engine.score !== null && engine.score !== undefined && (
                <p className="text-xs text-slate-600">
                  Score <span className="font-semibold text-slate-900">{engine.score}</span>
                  {engine.model_probability !== null && engine.model_probability !== undefined && (
                    <> · Probability {(engine.model_probability * 100).toFixed(1)}%</>
                  )}
                </p>
              )}
              {!engine.available && (
                <p className="text-xs text-slate-500">
                  Unavailable{engine.unavailable_reason ? `: ${engine.unavailable_reason}` : ''}
                  {engine.unavailable_detail ? ` — ${engine.unavailable_detail}` : ''}
                </p>
              )}
              {isSemantic && engine.embedding?.similarity !== undefined && (
                <p className="text-xs text-slate-600">
                  Embedding similarity {engine.embedding.similarity}
                  {engine.embedding.threshold !== undefined ? ` / ${engine.embedding.threshold} minimum` : ''}
                </p>
              )}
              {isSemantic && engine.llm?.response?.category_fit_score !== undefined && (
                <p className="text-xs text-slate-600">
                  LLM category fit {engine.llm.response.category_fit_score}
                </p>
              )}
              {findings.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-orange-700">
                  {findings.map((finding, index) => <li key={index}>• {finding}</li>)}
                </ul>
              )}
              {engine.combined_decision?.reason && (
                <p className="mt-2 text-xs text-slate-600">{engine.combined_decision.reason}</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ── Props ──────────────────────────────────────────────────────────────────

interface Props {
  apiFetch:       <T>(path: string, options?: RequestInit, impersonatedUPN?: string) => Promise<T>;
  formatCurrency: (amount: number) => string;
}

// ── Component ──────────────────────────────────────────────────────────────

export const HRBPReviewTab: React.FC<Props> = ({ apiFetch, formatCurrency }) => {
  const { isImpersonating, getEffectiveUser } = useImpersonation();
  const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;

  const [queue, setQueue]           = useState<HRBPQueueItem[]>([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [expanded, setExpanded]     = useState<number | null>(null);
  const [pairHistory, setPairHistory] = useState<Record<number, PairHistory>>({});
  const [historyLoading, setHistoryLoading] = useState<number | null>(null);
  const [reason, setReason]         = useState<Record<number, string>>({});
  const [deciding, setDeciding]     = useState<number | null>(null);
  const [decisionStatus, setDecisionStatus] = useState<Record<number, HRBPOutcome>>({});
  const [decisionHint, setDecisionHint] = useState<Record<number, boolean>>({});

  const loadQueue = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiFetch<HRBPQueueItem[]>('/api/hrbp/queue', {}, impersonatedUPN);
      setQueue(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load HRBP queue');
    } finally {
      setLoading(false);
    }
  }, [apiFetch, impersonatedUPN]);

  useEffect(() => { loadQueue(); }, [loadQueue]);

  const toggleExpand = async (nominationId: number) => {
    if (expanded === nominationId) {
      setExpanded(null);
      return;
    }
    setExpanded(nominationId);
    if (!pairHistory[nominationId]) {
      setHistoryLoading(nominationId);
      try {
        const data = await apiFetch<PairHistory>(`/api/hrbp/nominations/${nominationId}/pair-history`, {}, impersonatedUPN);
        setPairHistory(prev => ({ ...prev, [nominationId]: data }));
      } catch {
        // silently ignore — history panel will show empty
      } finally {
        setHistoryLoading(null);
      }
    }
  };

  const decide = async (nominationId: number, outcome: HRBPOutcome) => {
    if (!(reason[nominationId]?.trim())) {
      setDecisionHint(prev => ({ ...prev, [nominationId]: true }));
      window.setTimeout(() => setDecisionHint(prev => {
        const copy = { ...prev }; delete copy[nominationId]; return copy;
      }), 4000);
      return;
    }
    setDeciding(nominationId);
    try {
      await apiFetch(`/api/hrbp/nominations/${nominationId}/decision`, {
        method: 'POST',
        body: JSON.stringify({ outcome, reason: reason[nominationId] }),
      }, impersonatedUPN);
      setDecisionStatus(prev => ({ ...prev, [nominationId]: outcome }));
      // Remove from queue after short delay so the user sees the confirmation
      setTimeout(() => {
        setQueue(prev => prev.filter(n => n.nomination_id !== nominationId));
        setDecisionStatus(prev => { const copy = {...prev}; delete copy[nominationId]; return copy; });
      }, 1800);
    } catch (err) {
      alert(`Failed to record decision: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setDeciding(null);
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow-md p-8 text-center text-gray-500">
        Loading HRBP review queue…
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-lg shadow-md p-8 text-center text-red-600">
        {error}
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-md p-8">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <ShieldAlert className="w-6 h-6 text-orange-500" />
          HRBP Review Queue
          {queue.length > 0 && (
            <span className="ml-2 bg-orange-500 text-white text-sm rounded-full px-2.5 py-0.5">
              {queue.length}
            </span>
          )}
        </h2>
        <button
          onClick={loadQueue}
          className="text-sm text-gray-500 hover:text-gray-700 underline"
        >
          Refresh
        </button>
      </div>

      {queue.length === 0 ? (
        <div className="text-center py-16 text-gray-500">
          <CheckCircle className="w-14 h-14 text-green-300 mx-auto mb-3" />
          <p className="text-lg font-medium">No nominations pending review</p>
          <p className="text-sm mt-1">All flagged nominations have been resolved.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {queue.map(nom => {
            const decided = decisionStatus[nom.nomination_id];
            const fraudScope = (
              nom.review_scope === 'FRAUD'
              || nom.review_scope === 'FRAUD_AND_SEMANTIC'
              || nom.review_scope === 'LEGACY_FRAUD'
            );
            const semanticScope = (
              nom.review_scope === 'SEMANTIC'
              || nom.review_scope === 'FRAUD_AND_SEMANTIC'
            );
            return (
              <div
                key={nom.nomination_id}
                className={`border rounded-lg overflow-hidden transition-all ${
                  decided === 'CLEARED_NO_CONCERN' ? 'border-green-400 bg-green-50' :
                  decided === 'CLEARED_UNSUBSTANTIATED' ? 'border-blue-400 bg-blue-50' :
                  decided === 'CONFIRMED_CONCERN' || decided === 'CONFIRMED_SEMANTIC_CONCERN' ? 'border-red-400 bg-red-50' :
                  nom.risk_level === 'CRITICAL' ? 'border-red-300' :
                  'border-gray-200'
                }`}
              >
                {/* ── Header ── */}
                <div className="p-5">
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <p className="text-sm text-gray-500 mb-0.5">
                        Nomination #{nom.nomination_id} · {new Date(nom.nomination_date).toLocaleDateString()}
                      </p>
                      <p className="font-semibold text-gray-900 text-lg">
                        {nom.nominator_name} → {nom.beneficiary_name}
                      </p>
                      <p className="text-sm text-gray-500">{nom.nominator_email}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-2xl font-bold text-gray-800 mb-1">
                        {formatCurrency(nom.amount)}
                      </p>
                      <RiskBadge level={nom.risk_level} />
                      {nom.risk_level === 'CRITICAL' && (
                        <p className="mt-1 text-xs font-semibold text-red-700">
                          Priority human review
                        </p>
                      )}
                      {nom.fraud_score !== null && (
                        <p className="text-xs text-gray-500 mt-1">
                          Composite score: {nom.fraud_score}
                        </p>
                      )}
                    </div>
                  </div>

                  {/* Description */}
                  <p className="text-gray-700 text-sm bg-gray-50 rounded p-3 mb-3 border-l-4 border-blue-300">
                    {nom.description}
                  </p>

                  <EngineVerdicts item={nom} />

                  {/* Warning flags */}
                  {nom.warning_flags.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-3">
                      {nom.warning_flags.map((flag, i) => (
                        <span key={i} className="inline-block bg-orange-100 text-orange-800 text-xs px-2 py-0.5 rounded-full border border-orange-200">
                          ⚠ {flag}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* LLM explanation of the RF assessment */}
                  {nom.llm_explanation && (
                    <div className="mb-3 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-700">
                        LLM explanation
                      </p>
                      <p className="text-sm leading-relaxed text-slate-700">
                        {nom.llm_explanation}
                      </p>
                    </div>
                  )}

                  {/* SHAP model signal breakdown */}
                  <ShapPanel topFeaturesJson={nom.top_features} />

                  {/* Pair history toggle */}
                  <button
                    onClick={() => toggleExpand(nom.nomination_id)}
                    className="text-sm text-indigo-600 hover:text-indigo-800 flex items-center gap-1 mb-4"
                  >
                    {expanded === nom.nomination_id
                      ? <><ChevronUp className="w-4 h-4" /> Hide pair history</>
                      : <><ChevronDown className="w-4 h-4" /> Show pair history</>
                    }
                  </button>

                  {/* Pair history panel */}
                  {expanded === nom.nomination_id && (
                    <div className="mb-4 bg-gray-50 rounded-lg border border-gray-200 p-4">
                      {historyLoading === nom.nomination_id ? (
                        <p className="text-sm text-gray-400">Loading history…</p>
                      ) : pairHistory[nom.nomination_id] ? (
                        <>
                          <p className="text-sm font-semibold text-gray-700 mb-2">
                            {pairHistory[nom.nomination_id].pair_count} other nomination(s) between{' '}
                            {pairHistory[nom.nomination_id].nominator_name} and{' '}
                            {pairHistory[nom.nomination_id].beneficiary_name}
                          </p>
                          {pairHistory[nom.nomination_id].history.length === 0 ? (
                            <p className="text-sm text-gray-500">
                              No other nominations between {pairHistory[nom.nomination_id].nominator_name} and {pairHistory[nom.nomination_id].beneficiary_name}.
                            </p>
                          ) : (
                            <table className="w-full text-xs text-left">
                              <thead>
                                <tr className="text-gray-500 border-b border-gray-200">
                                  <th className="pb-1 pr-3">Date</th>
                                  <th className="pb-1 pr-3">Direction</th>
                                  <th className="pb-1 pr-3">Amount</th>
                                  <th className="pb-1 pr-3">Status</th>
                                  <th className="pb-1 pr-3">Risk</th>
                                  <th className="pb-1">Description</th>
                                </tr>
                              </thead>
                              <tbody>
                                {pairHistory[nom.nomination_id].history.map(h => (
                                  <tr key={h.nomination_id} className="border-b border-gray-100 last:border-0">
                                    <td className="py-1.5 pr-3 whitespace-nowrap text-gray-600">
                                      {new Date(h.nomination_date).toLocaleDateString()}
                                    </td>
                                    <td className="py-1.5 pr-3 whitespace-nowrap text-gray-700">
                                      {h.nominator_name} → {h.beneficiary_name}
                                    </td>
                                    <td className="py-1.5 pr-3 font-medium">{formatCurrency(h.amount)}</td>
                                    <td className="py-1.5 pr-3">
                                      <span className={`px-1.5 py-0.5 rounded text-xs ${
                                        h.status === 'Paid'     ? 'bg-green-100 text-green-700' :
                                        h.status === 'Approved' ? 'bg-blue-100 text-blue-700'   :
                                        h.status === 'Rejected' ? 'bg-red-100 text-red-700'     :
                                        'bg-gray-100 text-gray-600'
                                      }`}>{h.status}</span>
                                    </td>
                                    <td className="py-1.5 pr-3"><RiskBadge level={h.risk_level} /></td>
                                    <td className="py-1.5 text-gray-600 max-w-xs truncate" title={h.description}>
                                      {h.description}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </>
                      ) : (
                        <p className="text-sm text-gray-400">History unavailable.</p>
                      )}
                    </div>
                  )}

                  {/* Decision area */}
                  {decided ? (
                    <div className={`text-center py-3 rounded-lg font-semibold ${
                      decided === 'CLEARED_NO_CONCERN' ? 'text-green-700' :
                      decided === 'CLEARED_UNSUBSTANTIATED' ? 'text-blue-700' :
                      'text-red-700'
                    }`}>
                      {decided === 'CLEARED_NO_CONCERN'
                        ? '✅ Cleared — forwarded to manager and labelled legitimate'
                        : decided === 'CLEARED_UNSUBSTANTIATED'
                        ? '✅ Cleared — forwarded to manager and excluded from ML training'
                        : decided === 'CONFIRMED_SEMANTIC_CONCERN'
                        ? '❌ Semantic concern confirmed — nomination rejected and excluded from ML training'
                        : '❌ Integrity concern confirmed — nomination rejected'}
                    </div>
                  ) : !fraudScope && !semanticScope ? (
                    <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                      Review scope is unavailable. An administrator must check this nomination's integrity decision before an HRBP outcome can be recorded.
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <textarea
                        value={reason[nom.nomination_id] || ''}
                        onChange={e => {
                          setReason(prev => ({ ...prev, [nom.nomination_id]: e.target.value }));
                          if (e.target.value.trim()) setDecisionHint(prev => {
                            const copy = { ...prev };
                            delete copy[nom.nomination_id];
                            return copy;
                          });
                        }}
                        placeholder="Reason required for every HRBP decision…"
                        rows={2}
                        className="w-full text-sm px-3 py-2 border border-gray-300 rounded-lg resize-none focus:outline-none focus:ring-2 focus:ring-indigo-300"
                      />
                      {decisionHint[nom.nomination_id] && (
                        <div className="flex items-center gap-2 text-sm bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-3 py-2">
                          <AlertCircle className="w-4 h-4 flex-shrink-0" />
                          Please add a reason before recording the HRBP decision.
                        </div>
                      )}
                      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-4">
                        {fraudScope && (
                          <button
                            onClick={() => decide(nom.nomination_id, 'CLEARED_NO_CONCERN')}
                            disabled={deciding === nom.nomination_id}
                            className="flex flex-col items-center justify-center bg-green-600 hover:bg-green-700 text-white py-2 px-4 rounded-lg transition-colors disabled:bg-gray-300"
                          >
                            <span className="flex items-center gap-2 font-medium">
                              <CheckCircle className="w-4 h-4" />
                              Clear — no integrity issue
                            </span>
                            <span className="mt-0.5 text-xs text-green-100">Legitimate training label</span>
                          </button>
                        )}
                        <button
                          onClick={() => decide(nom.nomination_id, 'CLEARED_UNSUBSTANTIATED')}
                          disabled={deciding === nom.nomination_id}
                          className="flex flex-col items-center justify-center bg-blue-600 hover:bg-blue-700 text-white py-2 px-4 rounded-lg transition-colors disabled:bg-gray-300"
                        >
                          <span className="flex items-center gap-2 font-medium">
                            <CheckCircle className="w-4 h-4" />
                            Clear — concern not substantiated
                          </span>
                          <span className="mt-0.5 text-xs text-blue-100">Excluded from ML training</span>
                        </button>
                        {fraudScope && (
                          <button
                            onClick={() => decide(nom.nomination_id, 'CONFIRMED_CONCERN')}
                            disabled={deciding === nom.nomination_id}
                            className="flex flex-col items-center justify-center bg-red-600 hover:bg-red-700 text-white py-2 px-4 rounded-lg transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed"
                          >
                            <span className="flex items-center gap-2 font-medium">
                              <XCircle className="w-4 h-4" />
                              Confirm integrity concern
                            </span>
                            <span className="mt-0.5 text-xs text-red-100">Reject · Fraud training label</span>
                          </button>
                        )}
                        {semanticScope && (
                          <button
                            onClick={() => decide(nom.nomination_id, 'CONFIRMED_SEMANTIC_CONCERN')}
                            disabled={deciding === nom.nomination_id}
                            className="flex flex-col items-center justify-center bg-purple-600 hover:bg-purple-700 text-white py-2 px-4 rounded-lg transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed"
                          >
                            <span className="flex items-center gap-2 font-medium">
                              <XCircle className="w-4 h-4" />
                              Confirm semantic concern
                            </span>
                            <span className="mt-0.5 text-xs text-purple-100">Reject · Excluded from ML training</span>
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
