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
  Save, RefreshCw, AlertCircle, CheckCircle, X, Plus,
  ShieldCheck, History, UserCheck, Eye,
} from 'lucide-react';
import { getAccessToken } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type SubTab = 'organization' | 'roles' | 'categories' | 'fraud' | 'payroll' | 'audit';

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
  min_award:            number | null;
  max_award:            number | null;
}

const SUB_TABS: { id: SubTab; label: string; icon: React.ReactNode }[] = [
  { id: 'organization', label: 'Organization',     icon: <Settings className="w-4 h-4" /> },
  { id: 'roles',        label: 'Roles & Access',   icon: <UsersIcon className="w-4 h-4" /> },
  { id: 'categories',   label: 'Award Categories', icon: <Tag className="w-4 h-4" /> },
  { id: 'fraud',        label: 'Fraud / Integrity',icon: <ShieldAlert className="w-4 h-4" /> },
  { id: 'payroll',      label: 'Payroll',          icon: <DollarSign className="w-4 h-4" /> },
  { id: 'audit',        label: 'Audit & Access',   icon: <ShieldCheck className="w-4 h-4" /> },
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
      {sub === 'roles'        && <RolesPanel />}
      {sub === 'categories'   && <CategoriesPanel />}
      {sub === 'fraud'        && <FraudPanel />}
      {sub === 'payroll'      && <PayrollPanel />}
      {sub === 'audit'        && <AuditPanel />}
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
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Min award amount</label>
          <input
            type="number"
            value={data.min_award ?? ''}
            placeholder="50"
            onChange={e => setData(d => (d ? { ...d, min_award: e.target.value === '' ? null : Number(e.target.value) } : d))}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Max award amount</label>
          <input
            type="number"
            value={data.max_award ?? ''}
            placeholder="5000"
            onChange={e => setData(d => (d ? { ...d, max_award: e.target.value === '' ? null : Number(e.target.value) } : d))}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          />
        </div>
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

interface RoleMember { user_id: number; name: string; upn: string; roles: string[]; }
interface RoleUser   { user_id: number; name: string; upn: string; }
interface RolesData  { assignable_roles: string[]; members: RoleMember[]; users: RoleUser[]; }

const RolesPanel: React.FC = () => {
  const [data, setData]       = useState<RolesData | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy]       = useState(false);
  const [msg, setMsg]         = useState<{ type: 'ok' | 'err'; text: string } | null>(null);
  const [selUser, setSelUser] = useState<number | ''>('');
  const [selRole, setSelRole] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/roles`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const d: RolesData = await res.json();
      setData(d);
      setSelRole(r => r || (d.assignable_roles[0] ?? ''));
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Failed to load roles' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const change = async (action: 'grant' | 'revoke', user_id: number, role: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/roles/${action}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id, role }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setMsg({ type: 'ok', text: action === 'grant' ? 'Role granted.' : 'Role revoked.' });
      await load();
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Action failed' });
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 text-gray-400 text-sm py-12">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!data) return <div className="text-sm text-red-600 py-6">{msg?.text ?? 'No data.'}</div>;

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Grant */}
      <div className="flex flex-col sm:flex-row gap-2 sm:items-end">
        <div className="flex-1">
          <label className="block text-sm font-medium text-gray-700 mb-1">User</label>
          <select
            value={selUser}
            onChange={e => setSelUser(e.target.value ? Number(e.target.value) : '')}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value="">Select a user…</option>
            {data.users.map(u => (
              <option key={u.user_id} value={u.user_id}>{u.name} ({u.upn})</option>
            ))}
          </select>
        </div>
        <div className="sm:w-44">
          <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
          <select
            value={selRole}
            onChange={e => setSelRole(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            {data.assignable_roles.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <button
          disabled={busy || !selUser || !selRole}
          onClick={() => selUser && change('grant', Number(selUser), selRole)}
          style={{ backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' }}
          className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-medium disabled:opacity-50"
        >
          <Plus className="w-4 h-4" /> Grant
        </button>
      </div>

      {msg && (
        <div className={`flex items-center gap-2 text-sm ${msg.type === 'ok' ? 'text-green-700' : 'text-red-700'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      {/* Current assignments */}
      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2">Current assignments</h3>
        {data.members.length === 0 ? (
          <p className="text-sm text-gray-400 py-4">No app roles assigned yet.</p>
        ) : (
          <div className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
            {data.members.map(m => (
              <div key={m.user_id} className="flex items-center justify-between gap-3 p-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">{m.name}</p>
                  <p className="text-xs text-gray-500 truncate">{m.upn}</p>
                </div>
                <div className="flex flex-wrap gap-1.5 justify-end">
                  {m.roles.map(r => (
                    <span key={r} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 text-xs">
                      {r}
                      <button
                        disabled={busy}
                        onClick={() => change('revoke', m.user_id, r)}
                        title={`Revoke ${r}`}
                        className="text-gray-400 hover:text-red-600 disabled:opacity-50"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-gray-400">
        Admin access is managed in Microsoft Entra, not here. Every change is recorded in the
        organization's role history.
      </p>
    </div>
  );
};

interface Category {
  id: number;
  category_description: string;
  min_amount: number | null;
  max_amount: number | null;
  is_active: boolean;
}
interface CategoriesData {
  currency: string;
  org_min_award: number;
  org_max_award: number;
  categories: Category[];
}

const CategoriesPanel: React.FC = () => {
  const [data, setData]     = useState<CategoriesData | null>(null);
  const [rows, setRows]     = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | 'new' | null>(null);
  const [msg, setMsg]       = useState<{ type: 'ok' | 'err'; text: string } | null>(null);
  const [neu, setNeu]       = useState({ category_description: '', min_amount: '', max_amount: '', is_active: true });

  const load = useCallback(async () => {
    setLoading(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/categories`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const d: CategoriesData = await res.json();
      setData(d);
      setRows(d.categories.map(c => ({ ...c })));
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Failed to load categories' });
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const patchRow = (id: number, patch: Partial<Category>) =>
    setRows(rs => rs.map(r => (r.id === id ? { ...r, ...patch } : r)));

  const numOrNull = (v: string) => (v === '' ? null : Number(v));

  const saveRow = async (row: Category) => {
    setBusyId(row.id);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/categories/${row.id}`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category_description: row.category_description,
          min_amount: row.min_amount,
          max_amount: row.max_amount,
          is_active: row.is_active,
        }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setMsg({ type: 'ok', text: 'Saved.' });
      await load();
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Save failed' });
    } finally {
      setBusyId(null);
    }
  };

  const addNew = async () => {
    setBusyId('new');
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/categories`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category_description: neu.category_description,
          min_amount: numOrNull(neu.min_amount),
          max_amount: numOrNull(neu.max_amount),
          is_active: neu.is_active,
        }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setNeu({ category_description: '', min_amount: '', max_amount: '', is_active: true });
      setMsg({ type: 'ok', text: 'Category added.' });
      await load();
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Add failed' });
    } finally {
      setBusyId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 text-gray-400 text-sm py-12">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!data) return <div className="text-sm text-red-600 py-6">{msg?.text ?? 'No data.'}</div>;

  return (
    <div className="space-y-5">
      <p className="text-xs text-gray-500">
        Organization award range:{' '}
        <span className="font-medium">{data.org_min_award}–{data.org_max_award} {data.currency}</span>.
        Category limits must sit within this range; leave a field blank to inherit it.
      </p>

      {/* Add new */}
      <div className="border border-gray-200 rounded-lg p-3 space-y-2">
        <p className="text-sm font-semibold text-gray-700">Add category</p>
        <div className="flex flex-col sm:flex-row gap-2">
          <input value={neu.category_description} onChange={e => setNeu({ ...neu, category_description: e.target.value })}
            placeholder="Category name" className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm" />
          <input value={neu.min_amount} onChange={e => setNeu({ ...neu, min_amount: e.target.value })}
            type="number" placeholder="Min" className="sm:w-24 px-3 py-2 border border-gray-300 rounded-lg text-sm" />
          <input value={neu.max_amount} onChange={e => setNeu({ ...neu, max_amount: e.target.value })}
            type="number" placeholder="Max" className="sm:w-24 px-3 py-2 border border-gray-300 rounded-lg text-sm" />
          <button disabled={busyId === 'new' || !neu.category_description.trim()} onClick={addNew}
            style={{ backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' }}
            className="inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-medium disabled:opacity-50">
            <Plus className="w-4 h-4" /> Add
          </button>
        </div>
      </div>

      {msg && (
        <div className={`flex items-center gap-2 text-sm ${msg.type === 'ok' ? 'text-green-700' : 'text-red-700'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      {/* Existing categories */}
      {rows.length === 0 ? (
        <p className="text-sm text-gray-400 py-4">No categories yet.</p>
      ) : (
        <div className="space-y-2">
          {rows.map(row => (
            <div key={row.id}
              className={`border rounded-lg p-3 flex flex-col sm:flex-row sm:items-center gap-2 ${row.is_active ? 'border-gray-200' : 'border-gray-100 bg-gray-50'}`}>
              <input value={row.category_description}
                onChange={e => patchRow(row.id, { category_description: e.target.value })}
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm" />
              <input value={row.min_amount ?? ''} type="number" placeholder="Min"
                onChange={e => patchRow(row.id, { min_amount: e.target.value === '' ? null : Number(e.target.value) })}
                className="sm:w-24 px-3 py-2 border border-gray-300 rounded-lg text-sm" />
              <input value={row.max_amount ?? ''} type="number" placeholder="Max"
                onChange={e => patchRow(row.id, { max_amount: e.target.value === '' ? null : Number(e.target.value) })}
                className="sm:w-24 px-3 py-2 border border-gray-300 rounded-lg text-sm" />
              <label className="inline-flex items-center gap-1.5 text-sm text-gray-600 sm:w-24">
                <input type="checkbox" checked={row.is_active}
                  onChange={e => patchRow(row.id, { is_active: e.target.checked })} /> Active
              </label>
              <button disabled={busyId === row.id} onClick={() => saveRow(row)}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm border border-gray-300 text-gray-700 hover:bg-gray-100 disabled:opacity-50">
                <Save className="w-3.5 h-3.5" /> Save
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

interface FraudSettings {
  low_threshold: number;
  medium_threshold: number;
  high_threshold: number;
  critical_threshold: number;
  detection_window_days: number;
  use_char_count: boolean;
  min_char_count: number;
  min_word_count: number;
  category_alignment_threshold: number;
  duplicate_similarity_threshold: number;
  llm_category_check_enabled: boolean;
  llm_fit_threshold: number;
  llm_instructions: string | null;
  boilerplate_phrases: string[];
}

const FraudPanel: React.FC = () => {
  const [data, setData]         = useState<FraudSettings | null>(null);
  const [phrasesText, setPhrasesText] = useState('');
  const [loading, setLoading]   = useState(true);
  const [saving, setSaving]     = useState(false);
  const [msg, setMsg]           = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/fraud`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const d: FraudSettings = await res.json();
      setData(d);
      setPhrasesText((d.boilerplate_phrases || []).join('\n'));
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
      const body = {
        ...data,
        boilerplate_phrases: phrasesText.split('\n').map(p => p.trim()).filter(Boolean),
      };
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/fraud`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const d: FraudSettings = await res.json();
      setData(d);
      setPhrasesText((d.boilerplate_phrases || []).join('\n'));
      setMsg({ type: 'ok', text: 'Saved.' });
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Save failed' });
    } finally {
      setSaving(false);
    }
  };

  const numField = (key: keyof FraudSettings, label: string, opts?: { step?: number; min?: number; max?: number }) => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input
        type="number"
        step={opts?.step ?? 1}
        min={opts?.min}
        max={opts?.max}
        value={(data as any)[key] ?? ''}
        onChange={e => setData(d => (d ? { ...d, [key]: e.target.value === '' ? 0 : Number(e.target.value) } : d))}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
      />
    </div>
  );

  const checkField = (key: keyof FraudSettings, label: string) => (
    <label className="inline-flex items-center gap-2 text-sm text-gray-700">
      <input
        type="checkbox"
        checked={Boolean((data as any)[key])}
        onChange={e => setData(d => (d ? { ...d, [key]: e.target.checked } : d))}
      />
      {label}
    </label>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 text-gray-400 text-sm py-12">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!data) return <div className="text-sm text-red-600 py-6">{msg?.text ?? 'No data.'}</div>;

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg p-2">
        <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>Changes are saved immediately but the fraud pipeline caches config, so they take effect after the integrity-check service restarts.</span>
      </div>

      {/* Fraud score routing */}
      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2">Fraud score routing (0–100)</h3>
        <p className="text-xs text-gray-500 mb-3">A nomination's fraud score maps to a risk level at these cutoffs; they must be non-decreasing.</p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {numField('low_threshold', 'Low', { min: 0, max: 100 })}
          {numField('medium_threshold', 'Medium', { min: 0, max: 100 })}
          {numField('high_threshold', 'High', { min: 0, max: 100 })}
          {numField('critical_threshold', 'Critical', { min: 0, max: 100 })}
        </div>
        <div className="mt-3 sm:w-1/2">{numField('detection_window_days', 'Graph detection window (days)', { min: 1 })}</div>
      </div>

      {/* Description quality */}
      <div className="pt-2 border-t border-gray-100">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">Description quality checks</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {numField('min_word_count', 'Minimum words')}
          {numField('min_char_count', 'Minimum characters')}
          {numField('category_alignment_threshold', 'Category alignment threshold (0–1)', { step: 0.01, min: 0, max: 1 })}
          {numField('duplicate_similarity_threshold', 'Duplicate similarity threshold (0–1)', { step: 0.01, min: 0, max: 1 })}
          {numField('llm_fit_threshold', 'LLM fit threshold (0–1)', { step: 0.01, min: 0, max: 1 })}
        </div>
        <div className="flex flex-wrap gap-4 mt-3">
          {checkField('use_char_count', 'Use character count (CJK) instead of word count')}
          {checkField('llm_category_check_enabled', 'Enable LLM semantic check')}
        </div>
        <div className="mt-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">LLM instructions (optional)</label>
          <textarea
            rows={2}
            value={data.llm_instructions ?? ''}
            onChange={e => setData(d => (d ? { ...d, llm_instructions: e.target.value || null } : d))}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            placeholder="Extra guidance appended to the LLM prompt…"
          />
        </div>
        <div className="mt-3">
          <label className="block text-sm font-medium text-gray-700 mb-1">Boilerplate phrases (one per line)</label>
          <textarea
            rows={3}
            value={phrasesText}
            onChange={e => setPhrasesText(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono"
            placeholder={'great job\nwell done'}
          />
        </div>
      </div>

      {msg && (
        <div className={`flex items-center gap-2 text-sm ${msg.type === 'ok' ? 'text-green-700' : 'text-red-700'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      <div className="flex gap-2">
        <button onClick={save} disabled={saving}
          style={{ backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' }}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium disabled:opacity-50">
          <Save className={`w-4 h-4 ${saving ? 'animate-pulse' : ''}`} />{saving ? 'Saving…' : 'Save changes'}
        </button>
        <button onClick={load} disabled={saving || loading}
          className="px-4 py-2 rounded-lg text-gray-700 border border-gray-300 hover:bg-gray-50 disabled:opacity-50">Reset</button>
      </div>
    </div>
  );
};

interface PayrollProvider {
  id: number;
  name: string;
  display_name: string;
  company_id_at_provider: string | null;
  api_base_url: string | null;
}
interface PayrollStatus {
  provider: PayrollProvider | null;
  connected: boolean;
  token_expires_at: string | null;
  authorize_url: string | null;
}

const PayrollPanel: React.FC = () => {
  const [data, setData]     = useState<PayrollStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy]     = useState(false);
  const [msg, setMsg]       = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/payroll`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setData(await res.json());
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Failed to load payroll status' });
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const disconnect = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/payroll/disconnect`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      setMsg({ type: 'ok', text: 'Disconnected.' });
      await load();
    } catch (e: any) {
      setMsg({ type: 'err', text: e.message || 'Disconnect failed' });
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 text-gray-400 text-sm py-12">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (!data) return <div className="text-sm text-red-600 py-6">{msg?.text ?? 'No data.'}</div>;

  if (!data.provider) {
    return (
      <div className="text-center py-14 text-gray-400">
        <DollarSign className="w-10 h-10 mx-auto mb-3 opacity-40" />
        <p className="text-sm">No payroll provider is configured for this organization.</p>
        <p className="mt-1 text-xs">Provider assignment is handled during onboarding — contact support to add one.</p>
      </div>
    );
  }

  const p = data.provider;
  const expiry = data.token_expires_at ? new Date(data.token_expires_at).toLocaleString() : null;

  return (
    <div className="space-y-5 max-w-2xl">
      <div className="border border-gray-200 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-900">{p.display_name}</p>
            <p className="text-xs text-gray-500 truncate">{p.name}{p.api_base_url ? ` · ${p.api_base_url}` : ''}</p>
          </div>
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs shrink-0 ${data.connected ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
            {data.connected ? (<><CheckCircle className="w-3 h-3" />Connected</>) : 'Not connected'}
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs pt-1 border-t border-gray-100">
          <div>
            <span className="text-gray-400">Company ID</span>
            <p className="text-gray-700 font-mono break-all">{p.company_id_at_provider || '—'}</p>
          </div>
          <div>
            <span className="text-gray-400">Token expires</span>
            <p className="text-gray-700">{expiry || '—'}</p>
          </div>
        </div>
      </div>

      {msg && (
        <div className={`flex items-center gap-2 text-sm ${msg.type === 'ok' ? 'text-green-700' : 'text-red-700'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {data.authorize_url ? (
          <a
            href={data.authorize_url}
            style={{ backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' }}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium"
          >
            {data.connected ? 'Reconnect' : 'Connect'} {p.display_name}
          </a>
        ) : (
          <span className="text-xs text-gray-400">Connect link unavailable (payroll broker URL not configured).</span>
        )}
        {data.connected && (
          <button
            onClick={disconnect}
            disabled={busy}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-red-600 border border-red-200 hover:bg-red-50 disabled:opacity-50"
          >
            <X className="w-4 h-4" /> Disconnect
          </button>
        )}
      </div>

      <p className="text-xs text-gray-400">
        Connecting opens the provider's secure OAuth flow. Disconnect removes the stored token;
        the change is recorded in the payroll token history.
      </p>
    </div>
  );
};


// ── Audit & Access Review ─────────────────────────────────────────────────────
// Read-only SOC 2 views over the tenant's own data. Three sections, each fetched
// on demand: the current access snapshot, the role change timeline (from the
// UserRoles temporal history), and the impersonation audit trail.

type AuditSection = 'access' | 'history' | 'impersonation';

interface AccessRow {
  user_id: number; upn: string; name: string; title: string | null; role: string;
  granted_by: string | null; granted_at: string | null;
  updated_by: string | null; updated_at: string | null;
}
interface HistoryRow {
  user_id: number; upn: string | null; name: string; role: string;
  created_by: string | null; updated_by: string | null;
  valid_from: string | null; valid_to: string | null; active: boolean;
}
interface ImpRow {
  time: string | null; admin_upn: string; impersonated_upn: string;
  action: string; details: string | null; ip_address: string | null;
}

const AUDIT_SECTIONS: { id: AuditSection; label: string; icon: React.ReactNode; endpoint: string }[] = [
  { id: 'access',        label: 'Access Review',     icon: <UserCheck className="w-4 h-4" />, endpoint: 'audit/access-review' },
  { id: 'history',       label: 'Role History',      icon: <History className="w-4 h-4" />,   endpoint: 'audit/role-history' },
  { id: 'impersonation', label: 'Impersonation Log', icon: <Eye className="w-4 h-4" />,       endpoint: 'audit/impersonation' },
];

// UTC ISO (…Z) → localized string in the viewer's own timezone.
const fmtTime = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString() : '—';

const AuditPanel: React.FC = () => {
  const [section, setSection] = useState<AuditSection>('access');
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [note, setNote]       = useState<string | null>(null);
  const [rows, setRows]       = useState<any[]>([]);

  const load = useCallback(async (sec: AuditSection) => {
    const cfg = AUDIT_SECTIONS.find(s => s.id === sec)!;
    setLoading(true);
    setError(null);
    setRows([]);
    setNote(null);
    try {
      const token = await getAccessToken();
      const res = await fetch(`${API_BASE_URL}/api/admin/setup/${cfg.endpoint}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      const body = await res.json();
      setRows(body.rows || []);
      if (body.note) setNote(body.note);
    } catch (e: any) {
      setError(e.message || 'Failed to load audit data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(section); }, [section, load]);

  return (
    <div className="space-y-4">
      {/* Section selector */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex flex-wrap gap-1 p-1 bg-gray-100 rounded-lg">
          {AUDIT_SECTIONS.map(s => {
            const active = section === s.id;
            return (
              <button
                key={s.id}
                onClick={() => setSection(s.id)}
                style={active ? { backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' } : {}}
                className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  active ? '' : 'text-gray-600 hover:bg-gray-200'
                }`}
              >
                {s.icon}<span>{s.label}</span>
              </button>
            );
          })}
        </div>
        <button
          onClick={() => load(section)}
          disabled={loading}
          className="p-1.5 rounded hover:bg-gray-100 text-gray-500 disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {note && (
        <p className="text-xs text-gray-500 flex items-center gap-1.5">
          <ShieldCheck className="w-3.5 h-3.5 shrink-0" />{note}
        </p>
      )}

      {loading && (
        <div className="flex items-center justify-center gap-2 text-gray-400 text-sm py-12">
          <RefreshCw className="w-4 h-4 animate-spin" /> Loading…
        </div>
      )}

      {error && !loading && (
        <div className="flex items-start gap-2 p-3 bg-red-50 rounded-lg text-red-700 text-sm">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" /><span>{error}</span>
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <div className="text-center py-14 text-gray-400 text-sm">No records to show.</div>
      )}

      {/* ── Access Review ─────────────────────────────────────────── */}
      {!loading && !error && section === 'access' && rows.length > 0 && (
        <div className="overflow-x-auto border border-gray-100 rounded-lg">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wide">
              <tr>
                <th className="text-left font-medium px-3 py-2">User</th>
                <th className="text-left font-medium px-3 py-2">Role</th>
                <th className="text-left font-medium px-3 py-2">Granted by</th>
                <th className="text-left font-medium px-3 py-2">Granted</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(rows as AccessRow[]).map((r, i) => (
                <tr key={`${r.user_id}-${r.role}-${i}`} className="hover:bg-gray-50">
                  <td className="px-3 py-2">
                    <div className="font-medium text-gray-800">{r.name}</div>
                    <div className="text-xs text-gray-400">{r.upn}{r.title ? ` · ${r.title}` : ''}</div>
                  </td>
                  <td className="px-3 py-2">
                    <span className="inline-block px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 text-xs">{r.role}</span>
                  </td>
                  <td className="px-3 py-2 text-gray-600 text-xs">{r.granted_by || '—'}</td>
                  <td className="px-3 py-2 text-gray-500 text-xs whitespace-nowrap">{fmtTime(r.granted_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Role Change History (temporal) ────────────────────────── */}
      {!loading && !error && section === 'history' && rows.length > 0 && (
        <div className="space-y-2">
          {(rows as HistoryRow[]).map((r, i) => (
            <div key={i} className="border border-gray-100 rounded-lg p-3 text-sm">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium text-gray-800">{r.name}</span>
                <span className="inline-block px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 text-xs">{r.role}</span>
                {r.active ? (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-50 text-green-700 text-xs">
                    <CheckCircle className="w-3 h-3" />Active
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 text-xs">Ended</span>
                )}
              </div>
              <div className="mt-1.5 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-0.5 text-xs text-gray-500">
                <span>Effective: <span className="text-gray-700">{fmtTime(r.valid_from)}</span>{r.created_by ? ` · by ${r.created_by}` : ''}</span>
                {r.active
                  ? <span>Currently in effect</span>
                  : <span>Ended: <span className="text-gray-700">{fmtTime(r.valid_to)}</span>{r.updated_by ? ` · by ${r.updated_by}` : ''}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Impersonation Log ─────────────────────────────────────── */}
      {!loading && !error && section === 'impersonation' && rows.length > 0 && (
        <div className="space-y-2">
          {(rows as ImpRow[]).map((r, i) => (
            <div key={i} className="border border-gray-100 rounded-lg p-3 text-sm">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-gray-400 font-mono text-xs whitespace-nowrap">{fmtTime(r.time)}</span>
                <span className="inline-block px-2 py-0.5 rounded bg-purple-50 text-purple-700 text-xs">{r.action}</span>
              </div>
              <p className="mt-1.5 text-gray-700 text-xs break-words">
                <span className="font-medium">{r.admin_upn}</span>
                <span className="text-gray-400"> impersonated </span>
                <span className="font-medium">{r.impersonated_upn}</span>
                {r.ip_address ? <span className="text-gray-400"> · {r.ip_address}</span> : null}
              </p>
              {r.details && <p className="mt-1 text-xs text-gray-500 break-words">{r.details}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
