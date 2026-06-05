/**
 * NominationLogsDrawer
 *
 * Slide-in panel that shows the Log Analytics trace for a single nomination.
 * Triggered by admin clicking the #NominationId watermark on any nomination card.
 *
 * The backend endpoint GET /api/admin/nominations/{id}/logs handles the KQL query.
 * Log Analytics has a ~2 min ingestion delay — a note is shown to the admin.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { X, RefreshCw, AlertCircle, Info } from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface LogEntry {
  time:    string;
  level:   string;
  service: string;
  logger:  string;
  message: string;
}

interface LogsResponse {
  nomination_id:   number;
  hours:           number;
  log_count:       number;
  logs:            LogEntry[];
  ingestion_note:  string;
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

// Strip the "App_Log: " prefix and the trailing JSON extras blob for display.
// The extras are already shown as structured fields; the message alone is cleaner.
function cleanMessage(raw: string): string {
  return raw.replace(/^App_Log:\s*/, '').replace(/\s*\{.*\}$/, '').trim();
}

// Shorten service container name for display: "award-api-primary-sandbox" → "backend"
function shortService(service: string): string {
  if (service.includes('award-api'))         return 'backend';
  if (service.includes('integrity-check'))   return 'integrity-check';
  if (service.includes('auxiliary'))         return 'auxiliary';
  return service;
}

export const NominationLogsDrawer: React.FC<Props> = ({ nominationId, onClose }) => {
  const [data, setData]       = useState<LogsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [hours, setHours]     = useState(24);

  const fetchLogs = useCallback(async () => {
    if (nominationId === null) return;
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(
        `${API_BASE_URL}/api/admin/nominations/${nominationId}/logs?hours=${hours}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (e: any) {
      setError(e.message || 'Failed to fetch logs');
    } finally {
      setLoading(false);
    }
  }, [nominationId, hours]);

  // Fetch whenever the drawer opens or hours changes.
  useEffect(() => {
    if (nominationId !== null) fetchLogs();
  }, [fetchLogs]);

  // Close on Escape.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  if (nominationId === null) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 z-40"
        onClick={onClose}
      />

      {/* Drawer */}
      <div className="fixed top-0 right-0 h-full w-full max-w-2xl bg-white shadow-2xl z-50 flex flex-col">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-gray-50">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              Nomination Logs
              <span className="ml-2 font-mono text-gray-400 text-sm">#{nominationId}</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">Log Analytics trace across all services</p>
          </div>
          <div className="flex items-center gap-3">
            {/* Look-back window selector */}
            <select
              value={hours}
              onChange={e => setHours(Number(e.target.value))}
              className="text-xs border border-gray-300 rounded px-2 py-1 text-gray-600"
            >
              <option value={1}>Last 1 hour</option>
              <option value={6}>Last 6 hours</option>
              <option value={24}>Last 24 hours</option>
              <option value={72}>Last 3 days</option>
              <option value={168}>Last 7 days</option>
            </select>
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

        {/* Ingestion note */}
        <div className="flex items-start gap-2 px-6 py-2 bg-blue-50 border-b border-blue-100 text-xs text-blue-700">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>Log Analytics has a ~2 min ingestion delay. Logs for very recent nominations may be incomplete.</span>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4">

          {loading && (
            <div className="flex items-center justify-center h-40 text-gray-400 text-sm gap-2">
              <RefreshCw className="w-4 h-4 animate-spin" />
              Querying Log Analytics…
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
                {data.log_count} {data.log_count === 1 ? 'entry' : 'entries'} · last {data.hours}h
              </p>

              {data.log_count === 0 ? (
                <div className="text-center py-16 text-gray-400 text-sm">
                  <p>No logs found for nomination #{nominationId} in the last {data.hours} hours.</p>
                  <p className="mt-1">Try extending the look-back window.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {data.logs.map((log, i) => (
                    <div key={i} className="border border-gray-100 rounded-lg p-3 text-xs">
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        {/* Timestamp */}
                        <span className="text-gray-400 font-mono whitespace-nowrap">
                          {log.time.replace('T', ' ').slice(0, 19)}
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
                        {cleanMessage(log.message)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
};
