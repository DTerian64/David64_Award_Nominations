import React, { useState, useEffect } from 'react';
import { CheckCircle, Clock, Award, BarChart3, ShieldAlert, DollarSign, RefreshCw } from 'lucide-react';
import { Toast } from './components/Toast';
import {
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
  useMsal
} from '@azure/msal-react';
import { useTranslation } from 'react-i18next';
import { SignInButton } from './components/SignInButton';
import { IS_DEMO_SITE } from './components/DemoJoinPanel';
import { DemoRequestPage } from './components/DemoRequestPage';
import { DemoWelcomePage } from './components/DemoWelcomePage';
import { SignOutButton } from './components/SignOutButton';
import { AdminImpersonationPanel } from './components/AdminImpersonationPanel';
import { ImpersonationBanner } from './components/ImpersonationBanner';
import { AnalyticsDashboard } from './components/AnalyticsDashboard';
import { HRBPReviewTab } from './components/HRBPReviewTab';
import { useImpersonation } from './contexts/ImpersonationContext';
import { useTenantConfig } from './contexts/TenantConfigContext';
import { getAccessToken } from './services/api';
import { warmupDemoDatabase } from './services/demoWarmup';
import { NominationLogsDrawer } from './components/NominationLogsDrawer';

// Types matching your backend
interface User {
  UserId: number;
  userPrincipalName: string;
  FirstName: string;
  LastName: string;
  Title: string;
  ManagerId: number | null;
}

interface Nomination {
  NominationId: number;
  NominatorId: number;
  BeneficiaryId: number;
  ApproverId: number;
  Amount: number;
  Currency: string;
  NominationDescription: string;
  NominationDate: string;
  ApprovedDate: string | null;
  PayedDate: string | null;
  Status: 'Pending' | 'Submitted' | 'PendingHRBPReview' | 'Approved' | 'Paid' | 'Rejected';
  CategoryDescription?: string | null;
  RejectionReason?: string | null;
  RejectionActor?: string | null;
}

interface CurrentUser {
  UserId: number;
  userPrincipalName: string;
  FirstName: string;
  LastName: string;
  Title: string;
  ManagerId: number | null;
}

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// Authenticated API fetch with impersonation support
async function apiFetch<T>(path: string, options: RequestInit = {}, impersonatedUPN?: string): Promise<T> {
  try {
    const token = await getAccessToken();

    const headers = new Headers(options.headers);

    const hasBody = options.body !== undefined && options.body !== null;
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;

    if (hasBody && !isFormData && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    headers.set('Authorization', `Bearer ${token}`);

    if (impersonatedUPN) {
      headers.set('X-Impersonate-User', impersonatedUPN);
    }

    const res = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(errorData.detail || `HTTP ${res.status}`);
    }

    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  } catch (error) {
    console.error('API Error:', error);
    throw error;
  }
}

interface TenantBranding {
  tenant_name:          string;
  primary_color:        string | null;
  primary_hover_color:  string | null;
  primary_light_color:  string | null;
  primary_text_on_dark: string | null;
  company_logo_url:     string | null;
  tagline:              string | null;
}

const AwardNominationApp: React.FC = () => {
  // Hooks must always be called first — before any conditional return
  const { accounts } = useMsal();
  const { getEffectiveUser, isImpersonating, isAdmin } = useImpersonation();
  const { config, formatCurrency, minAmount, maxAmount } = useTenantConfig();
  const { t, i18n } = useTranslation();

  // ── Tenant branding (fetched before login, no auth required) ────────────────
  const [branding, setBranding] = useState<TenantBranding | null>(null);

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/tenant/branding`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setBranding(data); })
      .catch(() => {});   // silently ignore — branding is cosmetic
  }, []);

  const pathname = window.location.pathname;

  useEffect(() => {
    if (accounts.length === 0 && pathname !== '/demo/request' && pathname !== '/demo/welcome') {
      warmupDemoDatabase();
    }
  }, [accounts.length, pathname]);

  // Demo sub-pages — full-page layouts, rendered outside normal auth flow
  if (pathname === '/demo/request') return <DemoRequestPage />;
  if (pathname === '/demo/welcome') return <DemoWelcomePage />;

  // Format date according to the active locale
  const formatDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString(i18n.language);

  const [_currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [nominations, setNominations] = useState<Nomination[]>([]);
  const [nominationsLoading, setNominationsLoading] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState<Nomination[]>([]);
  const [decidedApprovals, setDecidedApprovals] = useState<Nomination[]>([]);
  const [approvalsView, setApprovalsView] = useState<'pending' | 'decided' | 'paid'>('pending');
  const [certLoadingId, setCertLoadingId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<'nominate' | 'history' | 'approvals' | 'hrbp' | 'analytics' | 'payroll'>('nominate');
  const [historyView, setHistoryView] = useState<'pending' | 'decided'>('pending');
  const [isHRBP, setIsHRBP] = useState(false);
  const [isPayrollBP, setIsPayrollBP] = useState(false);
  const [payrollProvider, setPayrollProvider] = useState<{ display_name: string; api_base_url: string; name: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [logsNominationId, setLogsNominationId] = useState<number | null>(null);

  // Payroll lookup state
  const [payrollUserId, setPayrollUserId]       = useState<string>('');
  const [payrollYear,   setPayrollYear]         = useState<number>(new Date().getFullYear());
  const [payrollMonth,  setPayrollMonth]        = useState<number>(new Date().getMonth() + 1);
  const [payrollResult, setPayrollResult]       = useState<{ profile: any; entries: any[]; year: number; month: number } | null>(null);
  const [payrollLoading, setPayrollLoading]     = useState(false);
  const [payrollError,   setPayrollError]       = useState<string | null>(null);

  // Reject reason dialog state
  const [rejectDialogNomId, setRejectDialogNomId] = useState<number | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  // Nomination form state
  const [selectedBeneficiary, setSelectedBeneficiary] = useState('');
  const [amount, setAmount] = useState('');
  const [description, setDescription] = useState('');
  const [selectedCategoryId, setSelectedCategoryId] = useState('');
  const [submitStatus, setSubmitStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  useEffect(() => {
    if (accounts.length > 0) {
      loadCurrentUser();
      loadUsers();
      loadNominations();
      loadPendingApprovals();
      loadMe();
    }
  }, [accounts, isImpersonating]);

  // Load decided approvals when the user opens the "Approved / Rejected" view.
  useEffect(() => {
    if (accounts.length > 0 && activeTab === 'approvals' && (approvalsView === 'decided' || approvalsView === 'paid')) {
      loadDecidedApprovals();
    }
  }, [accounts, isImpersonating, activeTab, approvalsView]);

  // Refresh "My Nominations" each time the tab is opened, so a just-created
  // nomination (Status 'Submitted') or a status change shows without a full reload.
  useEffect(() => {
    if (accounts.length > 0 && activeTab === 'history') {
      loadNominations();
    }
  }, [accounts, isImpersonating, activeTab]);

  const loadMe = async () => {
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const me = await apiFetch<{ is_hrbp: boolean; is_payroll_bp: boolean; is_admin: boolean; payroll_provider?: { display_name: string; api_base_url: string; name: string } | null }>('/api/me', {}, impersonatedUPN);
      setIsHRBP(me.is_hrbp);
      setIsPayrollBP(me.is_payroll_bp ?? false);
      setPayrollProvider(me.payroll_provider ?? null);
      // If switching away from a role-gated tab after impersonation change, reset to nominate
      setActiveTab(prev => {
        if (prev === 'hrbp'     && !me.is_hrbp)        return 'nominate';
        if (prev === 'payroll'  && !me.is_payroll_bp)   return 'nominate';
        if (prev === 'analytics' && !isAdmin)            return 'nominate';
        return prev;
      });
    } catch {
      setIsHRBP(false);
      setIsPayrollBP(false);
      setPayrollProvider(null);
    }
  };

  const loadEmployeePay = async () => {
    if (!payrollUserId || !payrollYear || !payrollMonth) return;
    setPayrollLoading(true);
    setPayrollError(null);
    setPayrollResult(null);
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const result = await apiFetch<{ profile: any; entries: any[]; year: number; month: number }>(
        `/api/payroll/employee-pay?user_id=${payrollUserId}&year=${payrollYear}&month=${payrollMonth}`,
        {},
        impersonatedUPN,
      );
      setPayrollResult({ ...result, year: payrollYear, month: payrollMonth });
    } catch (err: any) {
      setPayrollError(err.message || 'Failed to load payroll data');
    } finally {
      setPayrollLoading(false);
    }
  };

  const loadCurrentUser = async () => {
    try {
      const effectiveUPN = getEffectiveUser();
      setCurrentUser({
        UserId: 0,
        userPrincipalName: effectiveUPN,
        FirstName: accounts[0]?.name?.split(' ')[0] || '',
        LastName: accounts[0]?.name?.split(' ')[1] || '',
        Title: '',
        ManagerId: null,
      });
    } catch (error) {
      console.error('Failed to load current user:', error);
    }
  };

  const loadUsers = async () => {
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const userData = await apiFetch<User[]>('/api/users', {}, impersonatedUPN);
      setUsers(userData);
    } catch (error) {
      console.error('Failed to load users:', error);
    }
  };

  const loadNominations = async () => {
    setNominationsLoading(true);
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const history = await apiFetch<Nomination[]>('/api/nominations/history', {}, impersonatedUPN);
      setNominations(history);
    } catch (error) {
      console.error('Failed to load nominations:', error);
    } finally {
      setNominationsLoading(false);
    }
  };

  const loadPendingApprovals = async () => {
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const pending = await apiFetch<Nomination[]>('/api/nominations/pending', {}, impersonatedUPN);
      setPendingApprovals(pending);
    } catch (error) {
      console.error('Failed to load pending approvals:', error);
    }
  };

  const loadDecidedApprovals = async () => {
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const decided = await apiFetch<Nomination[]>('/api/nominations/my-approvals', {}, impersonatedUPN);
      setDecidedApprovals(decided);
    } catch (error) {
      console.error('Failed to load decided approvals:', error);
    }
  };

  const handleViewCertificate = async (nominationId: number) => {
    setCertLoadingId(nominationId);
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const { DownloadUrl } = await apiFetch<{ DownloadUrl: string; Cached: boolean }>(
        `/api/nominations/${nominationId}/certificate`, {}, impersonatedUPN
      );
      // Open the short-lived SAS link to the PDF in a new tab.
      window.open(DownloadUrl, '_blank', 'noopener');
    } catch (error: any) {
      setSubmitStatus({
        type: 'error',
        message: error.message || 'Failed to generate certificate. Please try again.',
      });
    } finally {
      setCertLoadingId(null);
    }
  };

  const handleSubmitNomination = async () => {
    const hasCategories = config.nomination_categories.length > 0;
    if (!selectedBeneficiary || !amount || !description || (hasCategories && !selectedCategoryId)) {
      setSubmitStatus({ type: 'error', message: t('messages.fillAllFields') });
      return;
    }

    const dollarAmount = Number(amount);
    if (dollarAmount < minAmount || dollarAmount > maxAmount) {
      setSubmitStatus({
        type: 'error',
        message: t('messages.amountRange', {
          min: formatCurrency(minAmount),
          max: formatCurrency(maxAmount),
        }),
      });
      return;
    }

    setLoading(true);
    setSubmitStatus(null);

    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;

      const payload: Record<string, unknown> = {
        BeneficiaryId: Number(selectedBeneficiary),
        Amount:  dollarAmount,
        NominationDescription: description,
      };
      if (config.nomination_categories.length > 0 && selectedCategoryId) {
        payload.CategoryId = Number(selectedCategoryId);
      }

      await apiFetch('/api/nominations', {
        method: 'POST',
        body: JSON.stringify(payload),
      }, impersonatedUPN);

      setSubmitStatus({ type: 'success', message: t('messages.submitSuccess') });
      setSelectedBeneficiary('');
      setAmount('');
      setDescription('');
      setSelectedCategoryId('');

      loadNominations();
      setTimeout(() => setSubmitStatus(null), 2000);
    } catch (error: any) {
      setSubmitStatus({
        type: 'error',
        message: error.message || t('messages.submitError'),
      });
    } finally {
      setLoading(false);
    }
  };

  const handleApproval = async (nominationId: number, approved: boolean, reason: string = '') => {
    setLoading(true);

    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;

      await apiFetch('/api/nominations/approve', {
        method: 'POST',
        body: JSON.stringify({ NominationId: nominationId, Approved: approved, reason }),
      }, impersonatedUPN);

      await loadPendingApprovals();
      await loadNominations();

      setSubmitStatus({
        type: 'success',
        message: approved ? t('messages.approveSuccess') : t('messages.rejectSuccess'),
      });

      setTimeout(() => setSubmitStatus(null), 3000);
    } catch (error: any) {
      setSubmitStatus({
        type: 'error',
        message: error.message || t('messages.approvalError'),
      });
    } finally {
      setLoading(false);
    }
  };

  const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
    const styles: Record<string, string> = {
      Pending:            'bg-yellow-100 text-yellow-800',
      Submitted:          'bg-yellow-100 text-yellow-800',
      PendingHRBPReview:  'bg-orange-100 text-orange-800',
      Approved:           'bg-green-100 text-green-800',
      Paid:               'bg-blue-100 text-blue-800',
      Rejected:           'bg-red-100 text-red-800',
    };
    return (
      <span className={`px-3 py-1 rounded-full text-xs font-semibold ${styles[status] || 'bg-gray-100 text-gray-800'}`}>
        {t(`status.${status}`, { defaultValue: status })}
      </span>
    );
  };

  const getUserName = (userId: number): string => {
    const user = users.find(u => u.UserId === userId);
    return user ? `${user.FirstName} ${user.LastName}` : 'Unknown';
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      <UnauthenticatedTemplate>
        <div className="min-h-screen flex items-center justify-center p-4">
          <div className="w-full max-w-2xl">
            <div className="flex rounded-lg shadow-lg overflow-hidden bg-white" style={{ minHeight: '380px' }}>

              {/* Left — brand panel */}
              <div
                className="flex flex-col items-center justify-center gap-4 p-10"
                style={{ width: '42%', backgroundColor: branding?.primary_color ?? '#1E2A3A' }}
              >
                {branding?.company_logo_url ? (
                  <img
                    src={branding.company_logo_url}
                    alt={branding.tenant_name}
                    className="h-16 object-contain"
                  />
                ) : (
                  <Award className="w-14 h-14 text-white opacity-90" />
                )}
                {branding?.tenant_name && (
                  <div className="text-center">
                    <p className="text-white text-lg font-semibold">{branding.tenant_name}</p>
                  </div>
                )}
              </div>

              {/* Right — sign-in panel */}
              <div className="flex flex-col justify-center gap-6 p-10" style={{ flex: 1 }}>
                <div>
                  <p className="text-xs text-gray-400 uppercase tracking-widest mb-2">{t('app.title')}</p>
                  <h1 className="text-2xl font-bold text-gray-900 leading-snug">
                    {branding?.tagline ?? t('app.subtitle')}
                  </h1>
                </div>

                {IS_DEMO_SITE ? (
                  <>
                    <SignInButton />
                    <a
                      href="/demo/request"
                      className="text-sm font-medium hover:underline text-center"
                      style={{ color: branding?.primary_color ?? 'var(--color-primary, #4f46e5)' }}
                    >
                      New to the demo? Request access →
                    </a>
                  </>
                ) : (
                  <SignInButton />
                )}

                <p className="text-xs text-gray-400">Use your organization account to continue.</p>
              </div>

            </div>
          </div>
        </div>
      </UnauthenticatedTemplate>

      <AuthenticatedTemplate>
        <ImpersonationBanner />

        <header className="bg-white shadow-sm border-b border-gray-200">
          <div className="max-w-7xl mx-auto px-4 py-4 sm:px-6 lg:px-8">
            <div className="flex justify-between items-center">
              <div className="flex items-center space-x-3">
                {branding?.company_logo_url ? (
                  <img
                    src={branding.company_logo_url}
                    alt={branding.tenant_name}
                    className="h-16 object-contain"
                  />
                ) : (
                  <Award className="w-8 h-8" style={{ color: branding?.primary_color ?? 'var(--color-primary)' }} />
                )}
                <div>
                  <h1 className="text-2xl font-bold text-gray-900">{t('app.title')}</h1>
                  {!branding?.company_logo_url && (
                    <p className="text-sm text-gray-600">
                      {branding?.tenant_name ?? t('app.subtitle')}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex flex-col items-end gap-1">
                <div className="flex items-center gap-3">
                  {accounts.length > 0 && (
                    <p className="text-sm font-semibold text-gray-900">{accounts[0].name}</p>
                  )}
                  <SignOutButton />
                </div>
                {isAdmin && <AdminImpersonationPanel users={users} />}
              </div>
            </div>
          </div>
        </header>

        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-6">
          <Toast
            toast={submitStatus}
            onDismiss={() => setSubmitStatus(null)}
          />

          {/* Tab bar */}
          <div className="bg-white rounded-lg shadow-sm p-1 flex space-x-1">
            {(['nominate', 'history', 'approvals'] as const).map((tab) => {
              const isActive = activeTab === tab;
              return (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  style={isActive ? {
                    backgroundColor: 'var(--color-primary)',
                    color: 'var(--color-primary-text)',
                  } : {}}
                  className={`flex-1 py-3 px-4 rounded-md font-medium transition-colors ${
                    isActive ? '' : 'text-gray-700 hover:bg-gray-100'
                  }`}
                >
                  {tab === 'nominate' && <Award className="w-5 h-5 inline-block mr-2" />}
                  {tab === 'history' && <Clock className="w-5 h-5 inline-block mr-2" />}
                  {tab === 'approvals' && <CheckCircle className="w-5 h-5 inline-block mr-2" />}
                  {t(`nav.${tab}`)}
                  {tab === 'approvals' && pendingApprovals.length > 0 && (
                    <span className="ml-2 bg-red-500 text-white text-xs rounded-full px-2 py-1">
                      {pendingApprovals.length}
                    </span>
                  )}
                </button>
              );
            })}
            {/* HRBP tab — driven by effective user's app_roles, works under impersonation */}
            {isHRBP && (
              <button
                onClick={() => setActiveTab('hrbp')}
                style={activeTab === 'hrbp' ? {
                  backgroundColor: 'var(--color-primary)',
                  color: 'var(--color-primary-text)',
                } : {}}
                className={`flex-1 py-3 px-4 rounded-md font-medium transition-colors ${
                  activeTab === 'hrbp' ? '' : 'text-gray-700 hover:bg-gray-100'
                }`}
              >
                <ShieldAlert className="w-5 h-5 inline-block mr-2" />
                {t('nav.hrbp')}
              </button>
            )}
            {/* Analytics tab — visible only to actual admin, never when impersonating */}
            {isAdmin && !isImpersonating && (
              <button
                onClick={() => setActiveTab('analytics')}
                style={activeTab === 'analytics' ? {
                  backgroundColor: 'var(--color-primary)',
                  color: 'var(--color-primary-text)',
                } : {}}
                className={`flex-1 py-3 px-4 rounded-md font-medium transition-colors ${
                  activeTab === 'analytics' ? '' : 'text-gray-700 hover:bg-gray-100'
                }`}
              >
                <BarChart3 className="w-5 h-5 inline-block mr-2" />
                {t('nav.analytics')}
              </button>
            )}
            {/* Payroll tab — visible only to PayrollBP role */}
            {isPayrollBP && (
              <button
                onClick={() => setActiveTab('payroll')}
                style={activeTab === 'payroll' ? {
                  backgroundColor: 'var(--color-primary)',
                  color: 'var(--color-primary-text)',
                } : {}}
                className={`flex-1 py-3 px-4 rounded-md font-medium transition-colors ${
                  activeTab === 'payroll' ? '' : 'text-gray-700 hover:bg-gray-100'
                }`}
              >
                <DollarSign className="w-5 h-5 inline-block mr-2" />
                {t('payroll.heading')}
              </button>
            )}
          </div>
        </div>

        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-6 pb-12">
          {/* ── Nominate tab ─────────────────────────────────────────────── */}
          {activeTab === 'nominate' && (
            <div className="bg-white rounded-lg shadow-md p-8">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">{t('nominate.heading')}</h2>

              <div className="space-y-6">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    {t('nominate.selectEmployee')}
                  </label>
                  <select
                    value={selectedBeneficiary}
                    onChange={(e) => setSelectedBeneficiary(e.target.value)}
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none"
                    style={{ accentColor: 'var(--color-primary)' }}
                  >
                    <option value="">{t('nominate.selectPlaceholder')}</option>
                    {users.map(user => (
                      <option key={user.UserId} value={user.UserId}>
                        {user.FirstName} {user.LastName} - {user.Title}
                      </option>
                    ))}
                  </select>
                </div>

                {config.nomination_categories.length > 0 && (
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">
                      Award Category
                    </label>
                    <select
                      value={selectedCategoryId}
                      onChange={(e) => setSelectedCategoryId(e.target.value)}
                      className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none"
                      style={{ accentColor: 'var(--color-primary)' }}
                    >
                      <option value="">Select a category…</option>
                      {config.nomination_categories.map((cat) => (
                        <option key={cat.id} value={cat.id}>
                          {cat.category_description}
                        </option>
                      ))}
                    </select>
                  </div>
                )}

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    {t('nominate.awardAmount')}
                  </label>
                  <input
                    type="number"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    min={minAmount}
                    max={maxAmount}
                    step="50"
                    placeholder={t('nominate.amountPlaceholder')}
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none"
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    {t('nominate.amountHint', {
                      min: formatCurrency(minAmount),
                      max: formatCurrency(maxAmount),
                    })}
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    {t('nominate.description')}
                  </label>
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={5}
                    placeholder={t('nominate.descriptionPlaceholder')}
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none resize-none"
                    maxLength={500}
                  />
                  <p className="mt-1 text-xs text-gray-500">
                    {t('nominate.charCount', { count: description.length })}
                  </p>
                </div>

                <button
                  onClick={handleSubmitNomination}
                  disabled={loading}
                  style={{
                    backgroundColor: loading ? undefined : 'var(--color-primary)',
                    color: 'var(--color-primary-text)',
                  }}
                  className="w-full py-3 px-6 rounded-lg font-semibold transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                  {loading ? t('nominate.submitting') : t('nominate.submit')}
                </button>
              </div>
            </div>
          )}

          {/* ── History tab ──────────────────────────────────────────────── */}
          {activeTab === 'history' && (
            <div className="bg-white rounded-lg shadow-md p-8">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold text-gray-900">{t('history.heading')}</h2>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => loadNominations()}
                    disabled={nominationsLoading}
                    title={t('history.refresh', { defaultValue: 'Refresh' })}
                    aria-label={t('history.refresh', { defaultValue: 'Refresh' })}
                    className="p-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                  >
                    <RefreshCw className={`w-4 h-4 ${nominationsLoading ? 'animate-spin' : ''}`} />
                  </button>
                  <select
                  value={historyView}
                  onChange={e => setHistoryView(e.target.value as 'pending' | 'decided')}
                  className="px-4 py-2 border border-gray-300 rounded-lg focus:outline-none text-sm text-gray-700"
                >
                  <option value="pending">{t('approvals.viewPending')}</option>
                  <option value="decided">{t('history.filterDecided', { defaultValue: 'Approved · Rejected · Paid' })}</option>
                </select>
                </div>
              </div>

              {(() => {
                const filtered = nominations.filter(n => {
                  if (historyView === 'pending')
                    return ['Submitted', 'PendingHRBPReview', 'Pending'].includes(n.Status);
                  return ['Approved', 'Rejected', 'Paid'].includes(n.Status);
                });

                if (filtered.length === 0) {
                  return (
                    <div className="text-center py-12">
                      <Award className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                      <p className="text-gray-600">{t('history.empty')}</p>
                    </div>
                  );
                }

                return (
                  <div className="space-y-4">
                    {filtered.map(nom => (
                      <div key={nom.NominationId} className="border border-gray-200 rounded-lg p-6 hover:shadow-md transition-shadow">
                        <div className="flex justify-between items-start mb-3">
                          <div>
                            <h3 className="text-lg font-semibold text-gray-900">
                              {getUserName(nom.BeneficiaryId)}
                            </h3>
                            <p className="text-sm text-gray-600">
                              {t('history.nominatedOn', { date: formatDate(nom.NominationDate) })}
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="text-2xl font-bold" style={{ color: 'var(--color-primary)' }}>
                              {formatCurrency(nom.Amount)}
                            </p>
                            <StatusBadge status={nom.Status} />
                          </div>
                        </div>
                        <p className="text-gray-700">{nom.NominationDescription}</p>
                        {nom.Status === 'Rejected' && (nom.RejectionActor || nom.RejectionReason) && (
                          <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg text-sm">
                            {nom.RejectionActor && (
                              <p className="font-semibold text-red-700 mb-1">
                                Rejected by: {nom.RejectionActor}
                              </p>
                            )}
                            {nom.RejectionReason && (
                              <p className="text-red-600">{nom.RejectionReason}</p>
                            )}
                          </div>
                        )}
                        <div className="flex items-center justify-between mt-3">
                          <div className="flex items-center gap-3">
                            {nom.CategoryDescription ? (
                              <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium"
                                    style={{ backgroundColor: 'var(--color-primary-light)', color: 'var(--color-primary)' }}>
                                {nom.CategoryDescription}
                              </span>
                            ) : <span />}
                            {isAdmin && (
                              <p
                                style={{ color: '#d1d5db', fontSize: '0.7rem', fontFamily: 'monospace', userSelect: 'all', cursor: 'pointer' }}
                                onClick={() => setLogsNominationId(nom.NominationId)}
                                title="View logs for this nomination"
                              >
                                #{nom.NominationId}
                              </p>
                            )}
                          </div>
                          {(nom.Status === 'Approved' || nom.Status === 'Paid') && (
                            <button
                              onClick={() => handleViewCertificate(nom.NominationId)}
                              disabled={certLoadingId === nom.NominationId}
                              className="inline-flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-lg border transition-colors cursor-pointer hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed"
                              style={{ color: 'var(--color-primary)', borderColor: 'var(--color-primary)' }}
                            >
                              <Award className="w-4 h-4" />
                              {certLoadingId === nom.NominationId
                                ? t('approvals.generatingCertificate')
                                : t('approvals.certificate')}
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>
          )}

          {/* ── Approvals tab ────────────────────────────────────────────── */}
          {activeTab === 'approvals' && (
            <div className="bg-white rounded-lg shadow-md p-8">
              {/* Heading dropdown: Pending Approvals vs Approved / Rejected */}
              <div className="flex items-center justify-between mb-6">
                <select
                  value={approvalsView}
                  onChange={(e) => setApprovalsView(e.target.value as 'pending' | 'decided' | 'paid')}
                  className="px-4 py-3 border border-gray-300 rounded-lg focus:outline-none cursor-pointer text-gray-900 font-medium"
                  style={{ accentColor: 'var(--color-primary)' }}
                >
                  <option value="pending">{t('approvals.viewPending')}</option>
                  <option value="decided">{t('approvals.viewDecided')}</option>
                  <option value="paid">{t('approvals.viewPaid')}</option>
                </select>
                {approvalsView === 'pending' && pendingApprovals.length > 0 && (
                  <span className="bg-red-500 text-white text-sm rounded-full px-3 py-1">
                    {pendingApprovals.length}
                  </span>
                )}
              </div>

              {/* Pending view */}
              {approvalsView === 'pending' && (
                pendingApprovals.length === 0 ? (
                  <div className="text-center py-12">
                    <CheckCircle className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                    <p className="text-gray-600">{t('approvals.empty')}</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {pendingApprovals.map(nom => (
                      <div key={nom.NominationId} className="border border-gray-200 rounded-lg p-6">
                        <div className="flex justify-between items-start mb-4">
                          <div>
                            <h3 className="text-lg font-semibold text-gray-900">
                              {getUserName(nom.BeneficiaryId)}
                            </h3>
                            <p className="text-sm text-gray-600">
                              {t('approvals.nominatedBy', {
                                name: getUserName(nom.NominatorId),
                                date: formatDate(nom.NominationDate),
                              })}
                            </p>
                          </div>
                          <p className="text-2xl font-bold" style={{ color: 'var(--color-primary)' }}>
                            {formatCurrency(nom.Amount)}
                          </p>
                        </div>
                        <p className="text-gray-700 mb-2">{nom.NominationDescription}</p>
                        <div className="flex items-end justify-between mb-4">
                          {nom.CategoryDescription ? (
                            <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium"
                                  style={{ backgroundColor: 'var(--color-primary-light)', color: 'var(--color-primary)' }}>
                              {nom.CategoryDescription}
                            </span>
                          ) : <span />}
                          {isAdmin && (
                            <p
                              style={{ color: '#d1d5db', fontSize: '0.7rem', fontFamily: 'monospace', userSelect: 'all', cursor: 'pointer' }}
                              onClick={() => setLogsNominationId(nom.NominationId)}
                              title="View logs for this nomination"
                            >
                              #{nom.NominationId}
                            </p>
                          )}
                        </div>
                        <div className="flex space-x-3">
                          <button
                            onClick={() => handleApproval(nom.NominationId, true)}
                            disabled={loading}
                            className="flex-1 bg-green-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-green-700 transition-colors disabled:bg-gray-400"
                          >
                            {t('approvals.approve')}
                          </button>
                          <button
                            onClick={() => { setRejectDialogNomId(nom.NominationId); setRejectReason(''); }}
                            disabled={loading}
                            className="flex-1 bg-red-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-red-700 transition-colors disabled:bg-gray-400"
                          >
                            {t('approvals.reject')}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )
              )}

              {/* Approved / Rejected view */}
              {approvalsView === 'decided' && (() => {
                const items = decidedApprovals.filter(n => n.Status === 'Approved' || n.Status === 'Rejected');
                return items.length === 0 ? (
                  <div className="text-center py-12">
                    <CheckCircle className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                    <p className="text-gray-600">{t('approvals.emptyDecided')}</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {items.map(nom => (
                      <div key={nom.NominationId} className="border border-gray-200 rounded-lg p-6">
                        <div className="flex justify-between items-start mb-3">
                          <div>
                            <h3 className="text-lg font-semibold text-gray-900">
                              {getUserName(nom.BeneficiaryId)}
                            </h3>
                            <p className="text-sm text-gray-600">
                              {t('approvals.nominatedBy', {
                                name: getUserName(nom.NominatorId),
                                date: formatDate(nom.NominationDate),
                              })}
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="text-2xl font-bold" style={{ color: 'var(--color-primary)' }}>
                              {formatCurrency(nom.Amount)}
                            </p>
                            <StatusBadge status={nom.Status} />
                          </div>
                        </div>
                        <p className="text-gray-700 mb-3">{nom.NominationDescription}</p>
                        {nom.Status === 'Rejected' && (nom.RejectionActor || nom.RejectionReason) && (
                          <div className="mb-3 p-3 bg-red-50 border border-red-200 rounded-lg text-sm">
                            {nom.RejectionActor && (
                              <p className="font-semibold text-red-700 mb-1">
                                Rejected by: {nom.RejectionActor}
                              </p>
                            )}
                            {nom.RejectionReason && (
                              <p className="text-red-600">{nom.RejectionReason}</p>
                            )}
                          </div>
                        )}
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            {nom.CategoryDescription ? (
                              <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium"
                                    style={{ backgroundColor: 'var(--color-primary-light)', color: 'var(--color-primary)' }}>
                                {nom.CategoryDescription}
                              </span>
                            ) : <span />}
                            {isAdmin && (
                              <p
                                style={{ color: '#d1d5db', fontSize: '0.7rem', fontFamily: 'monospace', userSelect: 'all', cursor: 'pointer' }}
                                onClick={() => setLogsNominationId(nom.NominationId)}
                                title="View logs for this nomination"
                              >
                                #{nom.NominationId}
                              </p>
                            )}
                          </div>
                          {(nom.Status === 'Approved' || nom.Status === 'Paid') && (
                            <button
                              onClick={() => handleViewCertificate(nom.NominationId)}
                              disabled={certLoadingId === nom.NominationId}
                              className="inline-flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-lg border transition-colors cursor-pointer hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed"
                              style={{ color: 'var(--color-primary)', borderColor: 'var(--color-primary)' }}
                            >
                              <Award className="w-4 h-4" />
                              {certLoadingId === nom.NominationId
                                ? t('approvals.generatingCertificate')
                                : t('approvals.certificate')}
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}

              {/* Paid view */}
              {approvalsView === 'paid' && (() => {
                const items = decidedApprovals.filter(n => n.Status === 'Paid');
                return items.length === 0 ? (
                  <div className="text-center py-12">
                    <CheckCircle className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                    <p className="text-gray-600">{t('approvals.emptyPaid')}</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {items.map(nom => (
                      <div key={nom.NominationId} className="border border-gray-200 rounded-lg p-6">
                        <div className="flex justify-between items-start mb-3">
                          <div>
                            <h3 className="text-lg font-semibold text-gray-900">
                              {getUserName(nom.BeneficiaryId)}
                            </h3>
                            <p className="text-sm text-gray-600">
                              {t('approvals.nominatedBy', {
                                name: getUserName(nom.NominatorId),
                                date: formatDate(nom.NominationDate),
                              })}
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="text-2xl font-bold" style={{ color: 'var(--color-primary)' }}>
                              {formatCurrency(nom.Amount)}
                            </p>
                            <StatusBadge status={nom.Status} />
                          </div>
                        </div>
                        <p className="text-gray-700 mb-3">{nom.NominationDescription}</p>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            {nom.CategoryDescription ? (
                              <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium"
                                    style={{ backgroundColor: 'var(--color-primary-light)', color: 'var(--color-primary)' }}>
                                {nom.CategoryDescription}
                              </span>
                            ) : <span />}
                            {isAdmin && (
                              <p
                                style={{ color: '#d1d5db', fontSize: '0.7rem', fontFamily: 'monospace', userSelect: 'all', cursor: 'pointer' }}
                                onClick={() => setLogsNominationId(nom.NominationId)}
                                title="View logs for this nomination"
                              >
                                #{nom.NominationId}
                              </p>
                            )}
                          </div>
                          <button
                            onClick={() => handleViewCertificate(nom.NominationId)}
                            disabled={certLoadingId === nom.NominationId}
                            className="inline-flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-lg border transition-colors cursor-pointer hover:bg-gray-50 disabled:opacity-60 disabled:cursor-not-allowed"
                            style={{ color: 'var(--color-primary)', borderColor: 'var(--color-primary)' }}
                          >
                            <Award className="w-4 h-4" />
                            {certLoadingId === nom.NominationId
                              ? t('approvals.generatingCertificate')
                              : t('approvals.certificate')}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>
          )}

          {/* ── HRBP Review tab ──────────────────────────────────────────── */}
          {activeTab === 'hrbp' && isHRBP && (
            <HRBPReviewTab apiFetch={apiFetch} formatCurrency={formatCurrency} />
          )}

          {/* ── Analytics tab ────────────────────────────────────────────── */}
          {activeTab === 'analytics' && isAdmin && !isImpersonating && (
            <div className="bg-white rounded-lg shadow-md p-8">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">{t('analytics.heading')}</h2>
              <AnalyticsDashboard />
            </div>
          )}

          {/* ── Payroll tab ──────────────────────────────────────────────── */}
          {activeTab === 'payroll' && isPayrollBP && (
            <div className="bg-white rounded-lg shadow-md p-8">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">
                {t('payroll.lookupHeading')}
                {payrollProvider && (
                  <span className="ml-2 text-2xl font-bold text-gray-500">
                    ({payrollProvider.display_name}
                    {payrollProvider.api_base_url && (
                      <> — {payrollProvider.api_base_url.replace(/^https?:\/\//, '')}</>
                    )})
                  </span>
                )}
              </h2>

              {/* Lookup controls */}
              <div className="flex flex-wrap gap-4 mb-6 items-end">
                <div className="flex-1 min-w-48">
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    {t('payroll.selectEmployee')}
                  </label>
                  <select
                    value={payrollUserId}
                    onChange={e => setPayrollUserId(e.target.value)}
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none"
                  >
                    <option value="">-- {t('payroll.selectEmployee')} --</option>
                    {users.map(u => (
                      <option key={u.UserId} value={u.UserId}>
                        {u.FirstName} {u.LastName}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    {t('payroll.month')}
                  </label>
                  <select
                    value={payrollMonth}
                    onChange={e => setPayrollMonth(Number(e.target.value))}
                    className="px-4 py-3 border border-gray-300 rounded-lg focus:outline-none"
                  >
                    {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
                      <option key={m} value={m}>
                        {new Date(2000, m - 1).toLocaleString(undefined, { month: 'long' })}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    {t('payroll.year')}
                  </label>
                  <select
                    value={payrollYear}
                    onChange={e => setPayrollYear(Number(e.target.value))}
                    className="px-4 py-3 border border-gray-300 rounded-lg focus:outline-none"
                  >
                    {Array.from({ length: 5 }, (_, i) => new Date().getFullYear() - i).map(y => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </div>

                <button
                  onClick={loadEmployeePay}
                  disabled={payrollLoading || !payrollUserId}
                  style={{ backgroundColor: 'var(--color-primary)', color: 'var(--color-primary-text)' }}
                  className="py-3 px-6 rounded-lg font-semibold transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                  {payrollLoading ? t('payroll.searching') : t('payroll.search')}
                </button>
              </div>

              {/* Error */}
              {payrollError && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
                  {payrollError}
                </div>
              )}

              {/* Prompt when nothing searched yet */}
              {!payrollResult && !payrollError && !payrollLoading && (
                <p className="text-gray-500 text-sm">{t('payroll.selectPrompt')}</p>
              )}

              {/* Results */}
              {payrollResult && (() => {
                const { profile, entries, year, month } = payrollResult;
                const offCycle   = entries.filter((e: any) => e.payroll_type === 'off_cycle');
                const monthLabel = new Date(year, month - 1).toLocaleString(undefined, { month: 'long' });

                return (
                  <div className="space-y-6 mt-6">
                    {/* ── Employee Card ── */}
                    {profile && (
                      <div className="border border-gray-200 rounded-lg p-5 bg-gray-50">
                        <h3 className="text-base font-semibold text-gray-900 mb-4">{t('payroll.employeeCard')}</h3>
                        <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
                          <div>
                            <span className="text-gray-500 block">{t('payroll.profileName')}</span>
                            <span className="font-medium text-gray-900">{profile.full_name || '—'}</span>
                          </div>
                          <div>
                            <span className="text-gray-500 block">{t('payroll.profileEmail')}</span>
                            <span className="font-medium text-gray-900">{profile.work_email || '—'}</span>
                          </div>
                          <div>
                            <span className="text-gray-500 block">{t('payroll.profileUUID')}</span>
                            <span className="font-mono text-xs text-gray-700">{profile.employee_uuid || '—'}</span>
                          </div>
                          <div>
                            <span className="text-gray-500 block">{t('payroll.profilePayrate')}</span>
                            <span className="font-medium text-gray-900">
                              {profile.payrate?.rate
                                ? `${formatCurrency(parseFloat(profile.payrate.rate))} / ${profile.payrate.payment_unit}`
                                : '—'}
                            </span>
                          </div>
                          <div className="col-span-2">
                            <span className="text-gray-500 block">{t('payroll.profileAddress')}</span>
                            <span className="font-medium text-gray-900">
                              {[
                                profile.address?.street_1,
                                profile.address?.street_2,
                                [profile.address?.city, profile.address?.state].filter(Boolean).join(', '),
                                profile.address?.zip,
                              ].filter(Boolean).join(' · ') || '—'}
                            </span>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── Award Payouts ── */}
                    <div className="border border-gray-200 rounded-lg p-5">
                      <h3 className="text-base font-semibold text-gray-900 mb-4">
                        {t('payroll.payrollCard')} — {monthLabel} {year}
                      </h3>
                      {offCycle.length === 0 ? (
                        <p className="text-gray-500 text-sm py-2">{t('payroll.noOffCycle')}</p>
                      ) : (
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm text-left border-collapse">
                            <thead>
                              <tr className="border-b border-gray-200 text-gray-600">
                                <th className="py-2 pr-4 font-semibold">{t('payroll.colPeriod')}</th>
                                <th className="py-2 pr-4 font-semibold">{t('payroll.colCheckDate')}</th>
                                <th className="py-2 pr-4 font-semibold">{t('payroll.colCompType')}</th>
                                <th className="py-2 pr-4 font-semibold text-right">{t('payroll.colGross')}</th>
                                <th className="py-2 pr-4 font-semibold text-right">{t('payroll.colDeductions')}</th>
                                <th className="py-2 pr-4 font-semibold text-right">{t('payroll.colNet')}</th>
                                <th className="py-2 font-semibold">{t('payroll.colPayrollRef')}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {offCycle.map((r: any) => (
                                <tr key={r.payroll_uuid} className="border-b border-gray-100 hover:bg-gray-50">
                                  <td className="py-2 pr-4 text-gray-700">{r.pay_period_start} – {r.pay_period_end}</td>
                                  <td className="py-2 pr-4 text-gray-700">{r.check_date ?? '—'}</td>
                                  <td className="py-2 pr-4 text-gray-700">{r.comp_type ?? '—'}</td>
                                  <td className="py-2 pr-4 text-right text-gray-700">{formatCurrency(r.gross_pay)}</td>
                                  <td className="py-2 pr-4 text-right text-gray-700">{formatCurrency(r.total_deductions)}</td>
                                  <td className="py-2 pr-4 text-right font-semibold" style={{ color: 'var(--color-primary)' }}>
                                    {formatCurrency(r.net_pay)}
                                  </td>
                                  <td className="py-2 font-mono text-xs text-gray-500" title={r.payroll_uuid}>
                                    {r.payroll_uuid}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })()}
            </div>
          )}
        </div>
        <NominationLogsDrawer
          nominationId={logsNominationId}
          onClose={() => setLogsNominationId(null)}
        />

        {/* ── Reject reason dialog ─────────────────────────────────────── */}
        {rejectDialogNomId !== null && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50">
            <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md mx-4">
              <h2 className="text-lg font-semibold text-gray-900 mb-2">
                {t('approvals.rejectDialogTitle', { defaultValue: 'Reject Nomination' })}
              </h2>
              <p className="text-sm text-gray-600 mb-4">
                {t('approvals.rejectDialogSubtitle', { defaultValue: 'Please provide a reason. The nominator will be notified.' })}
              </p>
              <textarea
                className="w-full border border-gray-300 rounded-lg p-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-red-400"
                rows={4}
                placeholder={t('approvals.rejectReasonPlaceholder', { defaultValue: 'e.g. This nomination does not meet the award criteria because…' })}
                value={rejectReason}
                onChange={e => setRejectReason(e.target.value)}
                autoFocus
              />
              <div className="flex gap-3 mt-4">
                <button
                  onClick={() => {
                    handleApproval(rejectDialogNomId, false, rejectReason);
                    setRejectDialogNomId(null);
                    setRejectReason('');
                  }}
                  disabled={loading || !rejectReason.trim()}
                  className="flex-1 bg-red-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-red-700 transition-colors disabled:bg-gray-400"
                >
                  {t('approvals.confirmReject', { defaultValue: 'Confirm Rejection' })}
                </button>
                <button
                  onClick={() => { setRejectDialogNomId(null); setRejectReason(''); }}
                  className="flex-1 bg-gray-100 text-gray-700 py-2 px-4 rounded-lg font-medium hover:bg-gray-200 transition-colors"
                >
                  {t('approvals.cancel', { defaultValue: 'Cancel' })}
                </button>
              </div>
            </div>
          </div>
        )}
      </AuthenticatedTemplate>
    </div>
  );
};

export default AwardNominationApp;
