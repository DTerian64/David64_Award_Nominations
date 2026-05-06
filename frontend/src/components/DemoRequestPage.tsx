/**
 * DemoRequestPage.tsx
 *
 * Standalone page at /demo/request (demo-awards.terian-services.com only).
 *
 * Visitor fills in Name + Email + Is Admin? and submits.
 * Backend sends a Microsoft B2B invitation email.
 * On success, shows a "Check your inbox" confirmation screen.
 */

import React, { useState } from 'react';
import { Award, Mail, ArrowLeft } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

type PageState = 'form' | 'submitting' | 'success' | 'error';

export const DemoRequestPage: React.FC = () => {
  const [firstName, setFirstName] = useState('');
  const [lastName,  setLastName]  = useState('');
  const [email,     setEmail]     = useState('');
  const [isAdmin,   setIsAdmin]   = useState(false);
  const [pageState, setPageState] = useState<PageState>('form');
  const [errorMsg,  setErrorMsg]  = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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
            <Award className="w-12 h-12 mx-auto mb-3" style={{ color: 'var(--color-primary, #4f46e5)' }} />
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
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="jane@yourcompany.com"
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
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
