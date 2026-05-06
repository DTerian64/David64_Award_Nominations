import React, { useState, useEffect } from 'react';
import { AlertCircle, CheckCircle, Clock, Award, BarChart3 } from 'lucide-react';
import {
  AuthenticatedTemplate,
  UnauthenticatedTemplate,
  useMsal
} from '@azure/msal-react';
import { useTranslation } from 'react-i18next';
import { SignInButton } from './components/SignInButton';
import { DemoJoinPanel, IS_DEMO_SITE } from './components/DemoJoinPanel';
import { SignOutButton } from './components/SignOutButton';
import { AdminImpersonationPanel } from './components/AdminImpersonationPanel';
import { ImpersonationBanner } from './components/ImpersonationBanner';
import { AnalyticsDashboard } from './components/AnalyticsDashboard';
import { useImpersonation } from './contexts/ImpersonationContext';
import { useTenantConfig } from './contexts/TenantConfigContext';
import { getAccessToken } from './services/api';

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
  Status: 'Pending' | 'Approved' | 'Paid' | 'Rejected';
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

const AwardNominationApp: React.FC = () => {
  const { accounts } = useMsal();
  const { getEffectiveUser, isImpersonating, isAdmin } = useImpersonation();
  const { formatCurrency, minAmount, maxAmount } = useTenantConfig();
  const { t, i18n } = useTranslation();

  // Format date according to the active locale
  const formatDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString(i18n.language);

  const [_currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [nominations, setNominations] = useState<Nomination[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<Nomination[]>([]);
  const [activeTab, setActiveTab] = useState<'nominate' | 'history' | 'approvals' | 'analytics'>('nominate');
  const [loading, setLoading] = useState(false);

  // Nomination form state
  const [selectedBeneficiary, setSelectedBeneficiary] = useState('');
  const [amount, setAmount] = useState('');
  const [description, setDescription] = useState('');
  const [submitStatus, setSubmitStatus] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  useEffect(() => {
    if (accounts.length > 0) {
      loadCurrentUser();
      loadUsers();
      loadNominations();
      loadPendingApprovals();
    }
  }, [accounts, isImpersonating]);

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
    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;
      const history = await apiFetch<Nomination[]>('/api/nominations/history', {}, impersonatedUPN);
      setNominations(history);
    } catch (error) {
      console.error('Failed to load nominations:', error);
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

  const handleSubmitNomination = async () => {
    if (!selectedBeneficiary || !amount || !description) {
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

      await apiFetch('/api/nominations', {
        method: 'POST',
        body: JSON.stringify({
          BeneficiaryId: Number(selectedBeneficiary),
          Amount:  dollarAmount,
          NominationDescription: description,
        }),
      }, impersonatedUPN);

      setSubmitStatus({ type: 'success', message: t('messages.submitSuccess') });
      setSelectedBeneficiary('');
      setAmount('');
      setDescription('');

      setTimeout(() => {
        loadNominations();
        setSubmitStatus(null);
      }, 2000);
    } catch (error: any) {
      setSubmitStatus({
        type: 'error',
        message: error.message || t('messages.submitError'),
      });
    } finally {
      setLoading(false);
    }
  };

  const handleApproval = async (nominationId: number, approved: boolean) => {
    setLoading(true);

    try {
      const impersonatedUPN = isImpersonating ? getEffectiveUser() : undefined;

      await apiFetch('/api/nominations/approve', {
        method: 'POST',
        body: JSON.stringify({ NominationId: nominationId, Approved: approved }),
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
      Pending:  'bg-yellow-100 text-yellow-800',
      Approved: 'bg-green-100 text-green-800',
      Paid:     'bg-blue-100 text-blue-800',
      Payed:    'bg-blue-100 text-blue-800',
      Rejected: 'bg-red-100 text-red-800',
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
          <div className="max-w-md w-full">
            <div className="bg-white p-8 rounded-lg shadow-lg">
              <div className="text-center mb-6">
                <Award className="w-16 h-16 mx-auto mb-4" style={{ color: 'var(--color-primary)' }} />
                <h1 className="text-3xl font-bold text-gray-900 mb-1">
                  {t('app.title')}
                </h1>
              </div>

              {IS_DEMO_SITE ? (
                <>
                  <p className="text-sm font-semibold text-gray-700 mb-4">Please Sign In</p>
                  <DemoJoinPanel />

                  {/* Separator */}
                  <div className="relative my-6">
                    <div className="absolute inset-0 flex items-center">
                      <div className="w-full border-t border-gray-300" />
                    </div>
                    <div className="relative flex justify-center text-sm">
                      <span className="px-3 bg-white text-gray-500">Already have an account?</span>
                    </div>
                  </div>

                  <SignInButton />
                </>
              ) : (
                <>
                  <p className="text-center text-gray-600 mb-6">{t('app.subtitle')}</p>
                  <div className="text-center">
                    <SignInButton />
                  </div>
                </>
              )}
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
                <Award className="w-8 h-8" style={{ color: 'var(--color-primary)' }} />
                <div>
                  <h1 className="text-2xl font-bold text-gray-900">{t('app.title')}</h1>
                  <p className="text-sm text-gray-600">{t('app.subtitle')}</p>
                </div>
              </div>
              <div className="flex items-center space-x-4">
                {isAdmin && <AdminImpersonationPanel users={users} />}
                {accounts.length > 0 && (
                  <div className="text-right">
                    <p className="text-sm font-semibold text-gray-900">{accounts[0].name}</p>
                    <p className="text-xs text-gray-600">{accounts[0].username}</p>
                  </div>
                )}
                <SignOutButton />
              </div>
            </div>
          </div>
        </header>

        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 mt-6">
          {submitStatus && (
            <div className={`mb-4 p-4 rounded-lg flex items-center ${
              submitStatus.type === 'success' ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'
            }`}>
              {submitStatus.type === 'success' ? (
                <CheckCircle className="w-5 h-5 mr-2" />
              ) : (
                <AlertCircle className="w-5 h-5 mr-2" />
              )}
              {submitStatus.message}
            </div>
          )}

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
            {isAdmin && (
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
              <h2 className="text-2xl font-bold text-gray-900 mb-6">{t('history.heading')}</h2>

              {nominations.length === 0 ? (
                <div className="text-center py-12">
                  <Award className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                  <p className="text-gray-600">{t('history.empty')}</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {nominations.map(nom => (
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
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Approvals tab ────────────────────────────────────────────── */}
          {activeTab === 'approvals' && (
            <div className="bg-white rounded-lg shadow-md p-8">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">{t('approvals.heading')}</h2>

              {pendingApprovals.length === 0 ? (
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
                      <p className="text-gray-700 mb-4">{nom.NominationDescription}</p>
                      <div className="flex space-x-3">
                        <button
                          onClick={() => handleApproval(nom.NominationId, true)}
                          disabled={loading}
                          className="flex-1 bg-green-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-green-700 transition-colors disabled:bg-gray-400"
                        >
                          {t('approvals.approve')}
                        </button>
                        <button
                          onClick={() => handleApproval(nom.NominationId, false)}
                          disabled={loading}
                          className="flex-1 bg-red-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-red-700 transition-colors disabled:bg-gray-400"
                        >
                          {t('approvals.reject')}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Analytics tab ────────────────────────────────────────────── */}
          {activeTab === 'analytics' && isAdmin && (
            <div className="bg-white rounded-lg shadow-md p-8">
              <h2 className="text-2xl font-bold text-gray-900 mb-6">{t('analytics.heading')}</h2>
              <AnalyticsDashboard />
            </div>
          )}
        </div>
      </AuthenticatedTemplate>
    </div>
  );
};

export default AwardNominationApp;
