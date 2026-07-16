/**
 * SetupPanel — admin configuration area (AWard_Nomination_Admin, own tenant only).
 *
 * Rendered only when the user is an admin AND not impersonating (App.tsx gates it),
 * and every write endpoint re-enforces both server-side. Sub-tabs are built
 * incrementally; Organization is the first working one.
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  Settings, Users as UsersIcon, Tag, ShieldAlert, DollarSign,
  Save, RefreshCw, AlertCircle, CheckCircle,
} from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type SubTab = 'organization' | 'roles' | 'categories' | 'fraud' | 'payroll';

interface OrgSettings {
  tenant_name:          string;
  tagline:              string | null;
  company_logo_url:     string | null;
  site_url:             string | null;
  fallback_admin_email: string | null;
  domain:               string | null;
  aad_tenant_id:        string | null;
  primary_color:        string | null;
  locale:               string | null;
  currency:             string | null;
}

const SUB_TABS: { id: SubTab; label: string; icon: React.ReactNode }[] = [
  { id: 'organization', label: 'Organization',     icon: <Settings className="w-4 h-4" /> },
  { id: 'roles',        label: 'Roles & Access',   icon: <UsersIcon className="w-4 h-4" /> },
  { id: 'categories',   label: 'Award Categories', icon: <Tag className="w-4 h-4" /> },
  { id: 'fraud',        label: 'Fraud / Integrity',icon: <ShieldAlert className="w-4 h-4" /> },
  { id: 'payroll',      label: 'Payroll',          icon: <DollarSign className="w-4 h-4" /> },
];

export const SetupPanel: React.FC = () => {
  const [sub, setSub] = useState<SubTab>('organization');
  return (
    <div className="bg-white rounded-lg shadow-md p-4 sm:p-6">
      {/* Sub-tab nav — 2-col grid on mobile, row on sm+ (same responsive pattern as the main tabs) */}
      <div className="grid grid-cols-2 gap-1 sm:flex sm:gap-1 mb-6 border-b border-gray-200 pb-3">
        {SUB_TABS.map(tab => {
          const active = sub === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setSub(tab.id)}
              style={active ? { backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' } : {}}
              className={`flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                active ? '' : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {sub === 'organization' && <OrganizationForm />}
      {sub === 'roles'        && <ComingSoon title="Roles & Access" />}
      {sub === 'categories'   && <ComingSoon title="Award Categories" />}
      {sub === 'fraud'        && <ComingSoon title="Fraud / Integrity" />}
      {sub === 'payroll'      && <ComingSoon title="Payroll Integration" />}
    </div>
  );
};

// ── Organization ────────────────────────────────────────────────────────────

const OrganizationForm: React.FC = () => {
  const [data, setData]     = useState<OrgSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [msg, setMsg]         = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/organization`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setData(await res.json());
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Failed to load settings' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!data) return;
    setSaving(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/organization`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setData(await res.json());
      setMsg({ type: 'ok', text: 'Saved.' });
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Save failed' });
    } finally {
      setSaving(false);
    }
  };

  const field = (
    key: keyof OrgSettings,
    label: string,
    opts?: { type?: string; placeholder?: string },
  ) => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={opts?.type ?? 'text'}
        value={(data?.[key] as string) ?? ''}
        placeholder={opts?.placeholder}
        onChange={e => setData(d => (d ? { ...d, [key]: e.target.value } : d))}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-gray-400"
      />
    </div>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 text-gray-400 text-sm py-12">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!data) {
    return <div className="text-sm text-red-600 py-6">{msg?.text ?? 'No data.'}</div>;
  }

  return (
    <div className="space-y-5 max-w-2xl">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {field('tenant_name', 'Organization name')}
        {field('tagline', 'Tagline')}
        {field('company_logo_url', 'Logo URL', { type: 'url', placeholder: 'https://…' })}
        {field('site_url', 'Site URL', { type: 'url', placeholder: 'https://…' })}
        {field('fallback_admin_email', 'Fallback admin email', { type: 'email' })}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Primary color</label>
          <div className="flex items-center gap-2">
            <input
              type="color"
              value={data.primary_color || '#2563eb'}
              onChange={e => setData(d => (d ? { ...d, primary_color: e.target.value } : d))}
              className="h-9 w-12 rounded border border-gray-300 p-0.5"
            />
            <input
              type="text"
              value={data.primary_color ?? ''}
              placeholder="#2563eb"
              onChange={e => setData(d => (d ? { ...d, primary_color: e.target.value } : d))}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
          </div>
        </div>
        {field('locale', 'Locale', { placeholder: 'en-US' })}
        {field('currency', 'Currency', { placeholder: 'USD' })}
      </div>

      {/* Read-only identity fields */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-3 border-t border-gray-100">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Domain (read-only)</label>
          <p className="text-sm text-gray-600 font-mono break-all">{data.domain || '—'}</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Entra tenant ID (read-only)</label>
          <p className="text-sm text-gray-600 font-mono break-all">{data.aad_tenant_id || '—'}</p>
        </div>
      </div>

      {msg && (
        <div className={`flex items-center gap-2 text-sm ${msg.type === 'ok' ? 'text-green-700' : 'text-red-700'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={saving}
          style={{ backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' }}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium disabled:opacity-50"
        >
          <Save className={`w-4 h-4 ${saving ? 'animate-pulse' : ''}`} />
          {saving ? 'Saving…' : 'Save changes'}
        </button>
        <button
          onClick={load}
          disabled={saving || loading}
          className="px-4 py-2 rounded-lg text-gray-700 border border-gray-300 hover:bg-gray-50 disabled:opacity-50"
        >
          Reset
        </button>
      </div>
    </div>
  );
};

const ComingSoon: React.FC<{ title: string }> = ({ title }) => (
  <div className="text-center py-16 text-gray-400">
    <Settings className="w-10 h-10 mx-auto mb-3 opacity-40" />
    <p className="text-sm">{title} — coming soon.</p>
  </div>
);
