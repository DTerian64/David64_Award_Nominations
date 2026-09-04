/**
 * NominationLogsDrawer
 *
 * Slide-in panel that shows the persistent log trail for a single nomination.
 * Triggered by an authorized analytics user clicking a nomination number.
 *
 * The tenant-scoped backend endpoint GET /api/admin/nominations/{id}/logs reads dbo.Nomination_Logs,
 * written at runtime by every service — full history, no retention window, no delay.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { X, RefreshCw, AlertCircle, Info } from 'lucide-react';
import { getAccessToken } from '../services/api';
import { SHAP_FEATURE_LABELS, parseShapContributions } from '../utils/shap';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface LogEntry {
  time:    string;
  level:   string;
  service: string;
  logger:  string;
  message: string;
  details: string;
}

interface LogsResponse {
  nomination_id:   number;
  integrity_check_only: boolean;
  log_count:       number;
  logs:            LogEntry[];
}

interface Props {
  nominationId: number | null;
  onClose: () => void;
}

const LEVEL_STYLES: Record<string, string> = {
  INFO:     'bg-blue-50 text-blue-700',
  WARNING:  'bg-yellow-50 text-yellow-700',
  ERROR:    'bg-red-50 text-red-700',
  CRITICAL: 'bg-red-100 text-red-900 font-bold',
};

// Fields too noisy or redundant to show as extras tags.
const SKIP_EXTRAS = new Set(['nomination_id', 'message_id', 'body', 'NominationId']);

/** Parse the structured `details` JSON column into an extras object. */
function parseDetails(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return {}; }
}

/** Render arbitrary nested details without JavaScript's "[object Object]" coercion. */
function formatDetailValue(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return '';
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]';
    if (value.every(item => item === null || typeof item !== 'object')) {
      return value.map(item => String(item)).join(', ');
    }
    return JSON.stringify(value, null, 2);
  }
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

/** Show the one finding that determines Graph score, with its pattern count. */
function GraphEvidence({ extras }: { extras: Record<string, unknown> }) {
  const findings = Array.isArray(extras.pattern_findings)
    ? extras.pattern_findings.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
    : [];
  const groups = new Map<string, { score: number; items: Record<string, unknown>[] }>();
  const add = (type: string, item: Record<string, unknown>, score: number) => {
    const group = groups.get(type) ?? { score: 0, items: [] };
    group.score = Math.max(group.score, score);
    group.items.push(item);
    groups.set(type, group);
  };
  findings.filter(item => item.routing_relevant !== false)
    .forEach(item => add(String(item.pattern_type), item, Number(item.finding_score) || 0));
  // Old logs contain warning strings only. Derive their winner and count.
  if (!findings.length && Array.isArray(extras.warning_flags)) {
    extras.warning_flags.forEach(raw => {
      const warning = String(raw);
      const match = warning.match(/:\s*(\w+)\s*\(([\d.]+),/);
      add(match?.[1] ?? 'Other', { warning }, Number(match?.[2]) || 0);
    });
  }
  const winner = extras.winning_finding && typeof extras.winning_finding === 'object'
    ? extras.winning_finding as Record<string, unknown> : null;
  const orderedGroups = [...groups.entries()].sort((a, b) => b[1].score - a[1].score);
  const winningType = extras.winning_pattern_type != null
    ? String(extras.winning_pattern_type)
    : orderedGroups[0]?.[0];
  const winningGroup = winningType ? groups.get(winningType) : undefined;
  const explicitCount = Number(extras.winning_pattern_count);
  const count = Number.isFinite(explicitCount) && explicitCount >= 0
    ? explicitCount : winningGroup?.items.length ?? 0;
  const score = extras.fraud_score != null ? Number(extras.fraud_score) : winningGroup?.score;
  const fallbackWinner = winningGroup?.items[0];
  const winningDetail = winner?.detail != null ? String(winner.detail)
    : fallbackWinner?.warning != null ? String(fallbackWinner.warning)
    : fallbackWinner?.detail != null ? String(fallbackWinner.detail) : null;
  if (!winningType) return null;
  return (
    <div className="mt-3 rounded border border-teal-200 bg-teal-50 p-2">
      <p className="font-semibold">Winning pattern: {winningType}{score !== undefined ? ` · ${score} / 100` : ''}</p>
      <p className="mt-1 text-gray-600">{count} relevant finding{count === 1 ? '' : 's'} · Score is the highest relevant finding, not their sum.</p>
      {winningDetail && <p className="mt-1">{winningDetail}</p>}
    </div>
  );
}

// Shorten service container name for display: "award-api-primary-sandbox" → "backend"
function shortService(service: string): string {
  if (service.includes('award-api'))         return 'backend';
  if (service.includes('integrity-check'))   return 'integrity-check';
  if (service.includes('auxiliary'))         return 'auxiliary';
  if (service.includes('payroll-broker'))    return 'payroll-broker';
  return service;
}

export const NominationLogsDrawer: React.FC<Props> = ({ nominationId, onClose }) => {
  const [isVisible, setIsVisible] = useState(false);
  const [data, setData]           = useState<LogsResponse | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [integrityCheckOnly, setIntegrityCheckOnly] = useState(false);

  // Drive the open/close CSS transition from nominationId.
  // requestAnimationFrame ensures the element is in the DOM before the
  // transform kicks in, giving the browser a frame to register translateX(100%)
  // before snapping to translateX(0) — without it the slide-in is skipped.
  useEffect(() => {
    if (nominationId !== null) {
      requestAnimationFrame(() => setIsVisible(true));
    } else {
      setIsVisible(false);
    }
  }, [nominationId]);

  const fetchLogs = useCallback(async () => {
    if (nominationId === null) return;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const token = await getAccessToken();
      const query = integrityCheckOnly ? '?integrity_check_only=true' : '';
      const res = await fetch(
        `${API_BASE_URL}/api/admin/nominations/${nominationId}/logs${query}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch logs');
    } finally {
      setLoading(false);
    }
  }, [nominationId, integrityCheckOnly]);

  // Fetch whenever the drawer opens.
  useEffect(() => {
    if (nominationId !== null) fetchLogs();
  }, [fetchLogs, nominationId]);

  // Close on Escape.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <>
      {/* Backdrop — fades in/out */}
      <div
        className="fixed inset-0 bg-black/30 z-40"
        style={{
          opacity:       isVisible ? 1 : 0,
          transition:    'opacity 300ms ease-in-out',
          pointerEvents: isVisible ? 'auto' : 'none',
        }}
        onClick={onClose}
      />

      {/* Drawer — slides in from the right */}
      <div
        className="fixed top-0 right-0 h-full w-full max-w-2xl bg-white shadow-2xl z-50 flex flex-col"
        style={{
          transform:  isVisible ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 300ms ease-in-out',
        }}
      >

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-gray-50">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              Nomination Logs
              <span className="ml-2 font-mono text-gray-400 text-sm">#{nominationId}</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">Persistent trail across all services</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={fetchLogs}
              disabled={loading}
              className="p-1.5 rounded hover:bg-gray-200 text-gray-500 disabled:opacity-40"
              title="Refresh"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-gray-200 text-gray-500"
              title="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Persistence note */}
        <div className="flex items-start gap-2 px-6 py-2 bg-blue-50 border-b border-blue-100 text-xs text-blue-700">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            {integrityCheckOnly
              ? 'Showing integrity-check processing and its Service Bus lifecycle messages.'
              : 'Showing the full nomination history across all services, including Service Bus activity.'}
          </span>
        </div>

        {/* Server-side logger namespace filter */}
        <label className="flex items-center gap-2 px-6 py-3 border-b border-gray-200 bg-white text-sm text-gray-700 cursor-pointer">
          <input
            type="checkbox"
            checked={integrityCheckOnly}
            onChange={(e) => setIntegrityCheckOnly(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300"
            style={{ accentColor: 'var(--color-primary)' }}
          />
          <span className="font-medium">Integrity check only</span>
          <span className="text-xs text-gray-400 font-mono">logger LIKE 'integrity_check%'</span>
        </label>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4">

          {loading && (
            <div className="flex items-center justify-center h-40 text-gray-400 text-sm gap-2">
              <RefreshCw className="w-4 h-4 animate-spin" />
              Loading logs…
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 p-4 bg-red-50 rounded-lg text-red-700 text-sm">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {data && !loading && (
            <>
              <p className="text-xs text-gray-400 mb-4">
                {data.log_count} {data.log_count === 1 ? 'entry' : 'entries'}
              </p>

              {data.log_count === 0 ? (
                <div className="text-center py-16 text-gray-400 text-sm">
                  <p>No logs found for nomination #{nominationId}.</p>
                  <p className="mt-1">Logs are written as the nomination is processed — refresh in a moment if it was just submitted.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {data.logs.map((log, i) => {
                    const text = log.message.replace(/^App_Log:\s*/, '');
                    const extras = parseDetails(log.details);
                    const shapContributions = parseShapContributions(extras.top_features);
                    const isGraph = text === 'Graph Analytics assessment completed';
                    const extraEntries = Object.entries(extras).filter(
                      ([k]) => !SKIP_EXTRAS.has(k)
                        && (k !== 'top_features' || shapContributions.length === 0)
                        && (!isGraph || ![
                          'warning_flags', 'winning_finding', 'winning_pattern_type',
                          'winning_pattern_count', 'detector_summary', 'pattern_findings',
                        ].includes(k))
                    );
                    return (
                      <div key={i} className="border border-gray-100 rounded-lg p-3 text-xs">
                        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                          {/* Timestamp */}
                          <span className="text-gray-400 font-mono whitespace-nowrap">
                            {log.time ? new Date(log.time).toLocaleString() : ''}
                          </span>
                          {/* Level badge */}
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${LEVEL_STYLES[log.level] ?? 'bg-gray-100 text-gray-600'}`}>
                            {log.level}
                          </span>
                          {/* Service badge */}
                          <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px]">
                            {shortService(log.service)}
                          </span>
                        </div>
                        {/* Message */}
                        <p className="text-gray-800 leading-relaxed break-words">
                          {text}
                        </p>
                        {isGraph && <GraphEvidence extras={extras} />}
                        {/* Structured extras — fraud score, risk level, etc. */}
                        {extraEntries.length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mt-2">
                            {extraEntries.map(([k, v]) => (
                              <span key={k} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-gray-50 border border-gray-200 text-xs font-mono text-gray-600">
                                <span className="text-gray-400">{k}</span>
                                <span className="text-gray-800 whitespace-pre-wrap break-all">
                                  {formatDetailValue(v)}
                                </span>
                              </span>
                            ))}
                          </div>
                        )}
                        {shapContributions.length > 0 && (
                          <div className="mt-2 rounded border border-gray-200 bg-gray-50 p-2">
                            <p className="mb-1.5 font-mono text-[10px] text-gray-400">top_features</p>
                            <div className="space-y-1.5">
                              {shapContributions.map((contribution, index) => {
                                const isRisk = contribution.contribution > 0;
                                const sign = isRisk ? '+' : '';
                                return (
                                  <div
                                    key={`${contribution.feature}-${index}`}
                                    className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 text-xs"
                                  >
                                    <div className="min-w-0">
                                      <p className="text-gray-700">
                                        {SHAP_FEATURE_LABELS[contribution.feature] ?? contribution.feature}
                                      </p>
                                      <p className="font-mono text-[10px] text-gray-400">
                                        value {contribution.raw_value}
                                      </p>
                                    </div>
                                    <span className={`font-mono ${isRisk ? 'text-orange-600' : 'text-emerald-600'}`}>
                                      {sign}{(contribution.contribution * 100).toFixed(1)} pp
                                    </span>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
};
