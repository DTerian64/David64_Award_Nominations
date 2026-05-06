/**
 * DemoJoinPanel.tsx
 *
 * Shown only on demo-awards.terian-services.com (hostname check).
 * Lets an external visitor create a demo account and sign in immediately.
 *
 * Flow:
 *   1. User fills: First Name, Last Name, Email, Is Admin? checkbox
 *   2. POST /api/demo/join  →  { upn, temp_password, aad_tenant_id }
 *   3. Show password-copy modal (password is shown exactly once)
 *   4. User clicks "Continue to Sign In"
 *   5. MSAL loginRedirect with loginHint=upn and demo-tenant authority
 *   6. Microsoft login page (email pre-filled) → user enters temp password
 *   7. Redirect back → authenticated
 */

import React, { useState } from 'react';
import { useMsal } from '@azure/msal-react';
import { loginRequest } from '../authConfig';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const IS_DEMO_SITE = window.location.hostname === 'demo-awards.terian-services.com';

interface JoinResult {
  upn:           string;
  temp_password: string;
  aad_tenant_id: string;
  user_id:       number;
}

// ---------------------------------------------------------------------------
// Password modal — shown after successful registration
// ---------------------------------------------------------------------------

interface PasswordModalProps {
  upn:           string;
  password:      string;
  aadTenantId:   string;
  onContinue:    () => void;
}

const PasswordModal: React.FC<PasswordModalProps> = ({
  upn, password, onContinue,
}) => {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(password);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-md w-full p-6">
        <div className="text-center mb-4">
          <div className="text-4xl mb-2">🎉</div>
          <h2 className="text-xl font-bold text-gray-900">Your demo account is ready!</h2>
          <p className="text-sm text-gray-500 mt-1">
            Save your password — it's shown only once.
          </p>
        </div>

        <div className="bg-gray-50 rounded-lg p-4 mb-4">
          <div className="mb-3">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              Username
            </p>
            <p className="text-sm font-mono text-gray-800 break-all">{upn}</p>
          </div>
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
              Password
            </p>
            <div className="flex items-center gap-2">
              <p className="text-sm font-mono text-gray-800 flex-1 break-all">{password}</p>
              <button
                onClick={copy}
                className="text-xs px-2 py-1 rounded border border-gray-300 hover:bg-gray-100 transition-colors whitespace-nowrap"
              >
                {copied ? '✓ Copied' : 'Copy'}
              </button>
            </div>
          </div>
        </div>

        <p className="text-xs text-amber-600 bg-amber-50 rounded p-2 mb-4">
          ⚠️ On the next screen, your email will be pre-filled.
          Enter the password above to complete sign-in.
        </p>

        <button
          onClick={onContinue}
          className="w-full py-3 px-4 rounded-lg font-semibold text-white transition-colors"
          style={{ backgroundColor: 'var(--color-primary, #4f46e5)' }}
        >
          Continue to Sign In →
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const DemoJoinPanel: React.FC = () => {
  const { instance } = useMsal();

  const [firstName,   setFirstName]   = useState('');
  const [lastName,    setLastName]    = useState('');
  const [email,       setEmail]       = useState('');
  const [isAdmin,     setIsAdmin]     = useState(false);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState<string | null>(null);
  const [joinResult,  setJoinResult]  = useState<JoinResult | null>(null);

  if (!IS_DEMO_SITE) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/demo/join`, {
        method: 'POST',
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

      const result: JoinResult = await res.json();
      setJoinResult(result);
    } catch (err: any) {
      setError(err.message || 'Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleContinueToSignIn = () => {
    if (!joinResult) return;

    instance.loginRedirect({
      ...loginRequest,
      // Override authority to the demo tenant so the token's tid matches
      authority: `https://login.microsoftonline.com/${joinResult.aad_tenant_id}`,
      loginHint: joinResult.upn,
    }).catch((err) => {
      console.error('MSAL redirect error:', err);
    });
  };

  return (
    <>
      {/* Password modal */}
      {joinResult && (
        <PasswordModal
          upn={joinResult.upn}
          password={joinResult.temp_password}
          aadTenantId={joinResult.aad_tenant_id}
          onContinue={handleContinueToSignIn}
        />
      )}

      {/* Join panel */}
      <div className="mt-6 border-t border-gray-200 pt-6">
        <div className="text-center mb-4">
          <p className="text-sm font-semibold text-gray-700">
            Explore Award Nominations — No IT setup required
          </p>
          <p className="text-xs text-gray-500 mt-1">
            Create a free demo account in seconds
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="First Name"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              required
              maxLength={50}
              className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
            <input
              type="text"
              placeholder="Last Name"
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              required
              maxLength={50}
              className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </div>

          <input
            type="email"
            placeholder="Your Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />

          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={isAdmin}
              onChange={(e) => setIsAdmin(e.target.checked)}
              className="w-4 h-4 rounded text-indigo-600 border-gray-300 focus:ring-indigo-400"
            />
            <span className="text-sm text-gray-700">
              Request Admin access
              <span className="text-xs text-gray-400 ml-1">(see all analytics &amp; controls)</span>
            </span>
          </label>

          {error && (
            <p className="text-xs text-red-600 bg-red-50 rounded p-2">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 px-4 rounded-lg font-semibold text-white text-sm transition-colors disabled:opacity-60"
            style={{ backgroundColor: 'var(--color-primary, #4f46e5)' }}
          >
            {loading ? 'Creating your account…' : 'Join as Demo User & Sign In'}
          </button>
        </form>
      </div>
    </>
  );
};
