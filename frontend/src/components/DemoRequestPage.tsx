/**
 * DemoRequestPage.tsx
 *
 * Standalone page at /demo/request (demo-awards.terian-services.com only).
 *
 * Visitor fills in Name + Email + Is Admin? and submits.
 * Backend sends a Microsoft B2B invitation email.
 * On success, shows a "Check your inbox" confirmation screen.
 */

import React, { useEffect, useState } from 'react';
import { Award, Mail, ArrowLeft } from 'lucide-react';
import { warmupDemoDatabase } from '../services/demoWarmup';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type PageState = 'form' | 'submitting' | 'success' | 'error';

// ---------------------------------------------------------------------------
// Personal / consumer email domain blocklist
// Keep in sync with demo_router.py PERSONAL_EMAIL_DOMAINS
// ---------------------------------------------------------------------------
const PERSONAL_EMAIL_DOMAINS = new Set([
  'gmail.com', 'googlemail.com',
  'yahoo.com', 'yahoo.co.uk', 'yahoo.co.in', 'yahoo.fr', 'yahoo.de',
  'yahoo.es', 'yahoo.it', 'yahoo.ca', 'yahoo.com.br', 'ymail.com',
  'hotmail.com', 'hotmail.co.uk', 'hotmail.fr', 'hotmail.de', 'hotmail.es',
  'outlook.com', 'live.com', 'live.co.uk', 'msn.com',
  'icloud.com', 'me.com', 'mac.com',
  'aol.com', 'aim.com',
  'protonmail.com', 'proton.me', 'pm.me',
  'mail.com', 'gmx.com', 'gmx.net', 'gmx.de',
  'zoho.com', 'fastmail.com', 'fastmail.fm',
  'tutanota.com', 'tutamail.com',
  'yandex.com', 'yandex.ru',
  'qq.com', '163.com', '126.com',
  'inbox.com', 'rocketmail.com',
]);

// Specific emails that bypass the personal-domain block (owner test accounts).
// Set VITE_DEMO_ALLOWED_EMAILS=addr1@x.com,addr2@y.com at build time.
const ALLOWED_EMAILS = new Set(
  (import.meta.env.VITE_DEMO_ALLOWED_EMAILS ?? '')
    .split(',')
    .map((e: string) => e.trim().toLowerCase())
    .filter(Boolean)
);

function isPersonalEmail(email: string): boolean {
  const normalised = email.trim().toLowerCase();
  if (ALLOWED_EMAILS.has(normalised)) return false;
  const domain = normalised.split('@')[1] ?? '';
  return PERSONAL_EMAIL_DOMAINS.has(domain);
}

export const DemoRequestPage: React.FC = () => {
  const [firstName,   setFirstName]   = useState('');
  const [lastName,    setLastName]    = useState('');
  const [email,       setEmail]       = useState('');
  const [emailError,  setEmailError]  = useState('');
  const [isAdmin,     setIsAdmin]     = useState(false);
  const [pageState,   setPageState]   = useState<PageState>('form');
  const [errorMsg,    setErrorMsg]    = useState('');
  const [logoUrl,     setLogoUrl]     = useState<string | null>(null);
  const [tenantName,  setTenantName]  = useState<string | null>(null);

  useEffect(() => {
    warmupDemoDatabase();
    fetch(`${API_BASE}/api/tenant/branding`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setLogoUrl(data.company_logo_url ?? null);
          setTenantName(data.tenant_name ?? null);
        }
      })
      .catch(() => {});
  }, []);

  const validateEmail = (value: string): string => {
    if (value && isPersonalEmail(value)) {
      return "That looks like a personal email address. Please use your work or school email to request demo access.";
    }
    return '';
  };

  const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setEmail(value);
    // Clear the error while typing so it doesn't nag on every keystroke;
    // we'll re-validate on blur and on submit.
    if (emailError) setEmailError('');
  };

  const handleEmailBlur = () => {
    setEmailError(validateEmail(email));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const emailValidationError = validateEmail(email);
    if (emailValidationError) {
      setEmailError(emailValidationError);
      return;
    }

    setPageState('submitting');
    setErrorMsg('');

    try {
      const res = await fetch(`${API_BASE}/api/demo/request`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          first_name: firstName.trim(),
          last_name:  lastName.trim(),
          email:      email.trim(),
          is_admin:   isAdmin,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Error ${res.status}`);
      }

      setPageState('success');
    } catch (err: any) {
      setErrorMsg(err.message || 'Something went wrong. Please try again.');
      setPageState('error');
    }
  };

  // ── Success screen ──────────────────────────────────────────────────────────
  if (pageState === 'success') {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-4">
        <div className="max-w-md w-full bg-white rounded-xl shadow-lg p-8 text-center">
          <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <Mail className="w-8 h-8 text-green-600" />
          </div>
          <h2 className="text-2xl font-bold text-gray-900 mb-2">Check your inbox</h2>
          <p className="text-gray-600 mb-6">
            We've sent an invitation to <strong>{email}</strong>.
            Click the link in that email to activate your demo access.
          </p>
          <p className="text-sm text-gray-400">
            The invitation comes from Microsoft — check your spam folder if you don't see it within a few minutes.
          </p>
          <button
            onClick={() => window.location.href = '/'}
            className="mt-6 text-sm text-indigo-600 hover:underline"
          >
            ← Back to sign in
          </button>
        </div>
      </div>
    );
  }

  // ── Form ────────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-4">
      <div className="max-w-md w-full">
        <div className="bg-white rounded-xl shadow-lg p-8">

          {/* Header */}
          <div className="text-center mb-6">
            {logoUrl
              ? <img src={logoUrl} alt={tenantName ?? ''} className="h-12 mx-auto mb-3 object-contain" />
              : <Award className="w-12 h-12 mx-auto mb-3" style={{ color: 'var(--color-primary, #4f46e5)' }} />
            }
            <h1 className="text-2xl font-bold text-gray-900">Request Demo Access</h1>
            <p className="text-sm text-gray-500 mt-1">
              Get hands-on access to the Award Nominations platform — no IT setup required.
            </p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs font-semibold text-gray-600 mb-1">First Name</label>
                <input
                  type="text"
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                  required
                  maxLength={50}
                  placeholder="Jane"
                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
                />
              </div>
              <div className="flex-1">
                <label className="block text-xs font-semibold text-gray-600 mb-1">Last Name</label>
                <input
                  type="text"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  required
                  maxLength={50}
                  placeholder="Smith"
                  className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">Work Email</label>
              <input
                type="email"
                value={email}
                onChange={handleEmailChange}
                onBlur={handleEmailBlur}
                required
                placeholder="jane@yourcompany.com"
                aria-describedby={emailError ? "email-error" : undefined}
                className={`w-full px-3 py-2 text-sm border rounded-lg focus:outline-none focus:ring-2 transition-colors ${
                  emailError
                    ? 'border-red-400 focus:ring-red-300 bg-red-50'
                    : 'border-gray-300 focus:ring-indigo-400'
                }`}
              />
              {emailError ? (
                <p id="email-error" className="mt-1.5 text-xs text-red-600 flex items-start gap-1.5">
                  <svg className="w-3.5 h-3.5 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                  </svg>
                  {emailError}
                </p>
              ) : (
                <p className="mt-1 text-xs text-gray-400">Use your work or school email address</p>
              )}
            </div>

            <label className="flex items-start gap-3 cursor-pointer select-none p-3 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors">
              <input
                type="checkbox"
                checked={isAdmin}
                onChange={(e) => setIsAdmin(e.target.checked)}
                className="mt-0.5 w-4 h-4 rounded text-indigo-600 border-gray-300"
              />
              <span>
                <span className="text-sm font-medium text-gray-800">Request Admin access</span>
                <span className="block text-xs text-gray-500 mt-0.5">
                  See analytics, fraud detection, impersonation controls, and all admin features
                </span>
              </span>
            </label>

            {pageState === 'error' && (
              <p className="text-xs text-red-600 bg-red-50 rounded-lg p-3">{errorMsg}</p>
            )}

            <button
              type="submit"
              disabled={pageState === 'submitting'}
              className="w-full py-3 px-4 rounded-lg font-semibold text-white text-sm transition-colors disabled:opacity-60"
              style={{ backgroundColor: 'var(--color-primary, #4f46e5)' }}
            >
              {pageState === 'submitting' ? 'Sending invitation…' : 'Request Demo Access'}
            </button>
          </form>

          {/* Back link */}
          <div className="mt-5 text-center">
            <button
              onClick={() => window.location.href = '/'}
              className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
            >
              <ArrowLeft className="w-3 h-3" />
              Already have access? Sign In
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
