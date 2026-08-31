/**
 * DemoWelcomePage.tsx
 *
 * Landing page at /demo/welcome — shown after a visitor accepts the
 * Microsoft B2B invitation email (inviteRedirectUrl points here).
 *
 * Flow on "Explore →" click:
 *   1. If MSAL already has a cached token → navigate to / directly.
 *   2. Try ssoSilent() with the demo tenant authority — picks up the existing
 *      Microsoft session from B2B redemption without showing a login page.
 *   3. If ssoSilent fails (no usable session, third-party cookies blocked,
 *      etc.) → loginRedirect fallback.  The loginHint pre-selects the
 *      account so the user usually skips the picker even on the fallback.
 *
 * Why loginHint matters:
 *   ssoSilent and loginRedirect cannot pick an account on their own when
 *   the browser has multiple cached AAD sessions.  Without loginHint, AAD
 *   returns `interaction_required` from the silent endpoint and shows
 *   "Pick an account" on the interactive one.  The backend appends the
 *   visitor's email as ?email=… on the inviteRedirectUrl precisely so we
 *   can pass it here as loginHint.  AAD resolves the original email
 *   against the B2B guest's `mail` attribute and routes silent auth to
 *   the correct #EXT# UPN under the hood — no need to know the UPN.
 *
 * ssoSilent is intentionally deferred to click time, not mount time, because
 * MSAL rejects ssoSilent calls while handleRedirectPromise is still settling
 * (which is the case immediately after the B2B redemption redirect lands here).
 */

import React, { useState } from 'react';
import { useMsal } from '@azure/msal-react';
import { Award, CheckCircle, Loader2 } from 'lucide-react';
import { loginRequest } from '../authConfig';

const DEMO_AAD_TENANT_ID = import.meta.env.VITE_DEMO_AAD_TENANT_ID as string | undefined;

/**
 * Read the visitor's email from the inviteRedirectUrl query string.
 * Set by the backend in _build_invite_redirect_url(...) so MSAL has a hint
 * about which AAD account to silently authenticate against.
 */
function readLoginHintFromUrl(): string | undefined {
  if (typeof window === 'undefined') return undefined;
  const raw = new URLSearchParams(window.location.search).get('email');
  if (!raw) return undefined;
  const trimmed = raw.trim();
  // Basic sanity check — refuse anything that doesn't look like an email so we
  // don't feed garbage into MSAL and trigger a confusing error.
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) return undefined;
  return trimmed;
}

export const DemoWelcomePage: React.FC = () => {
  const { instance, accounts } = useMsal();
  const [exploring, setExploring] = useState(false);

  const loginHint = readLoginHintFromUrl();

  const demoLoginRequest = {
    ...loginRequest,
    ...(DEMO_AAD_TENANT_ID && {
      authority: `https://login.microsoftonline.com/${DEMO_AAD_TENANT_ID}`,
    }),
    ...(loginHint && { loginHint }),
  };

  const handleExplore = async () => {
    setExploring(true);

    // Already authenticated — go straight to the app
    if (accounts.length > 0) {
      window.location.href = '/';
      return;
    }

    // Try silent SSO first — picks up the Microsoft session from B2B redemption.
    // With loginHint set, AAD resolves the original email to the B2B guest's
    // #EXT#@<tenant>.onmicrosoft.com UPN and issues tokens silently.
    try {
      await instance.ssoSilent(demoLoginRequest);
      window.location.href = '/';
    } catch {
      // No usable session (third-party cookies blocked, expired AAD session,
      // etc.) — full interactive login. loginHint pre-selects the account so
      // the user usually skips the "Pick an account" picker.
      instance.loginRedirect(demoLoginRequest).catch((err) => {
        console.error('MSAL redirect error:', err);
        setExploring(false);
      });
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-4">
      <div className="max-w-lg w-full bg-white rounded-xl shadow-lg p-10 text-center">

        <div className="flex justify-center mb-4">
          <div className="relative">
            <Award className="w-16 h-16" style={{ color: 'var(--color-primary, #4f46e5)' }} />
            <CheckCircle className="w-6 h-6 text-green-500 absolute -bottom-1 -right-1 bg-white rounded-full" />
          </div>
        </div>

        <h1 className="text-3xl font-bold text-gray-900 mb-2">
          Welcome to Award Nominations
        </h1>
        <p className="text-gray-500 mb-8">
          Your demo access is ready. You're about to explore a live SaaS platform
          for employee recognition — nominations, approvals, analytics, and fraud detection.
        </p>

        <div className="bg-indigo-50 rounded-lg p-4 mb-8 text-left space-y-2">
          {[
            'Submit and approve award nominations',
            'Explore real-time analytics and spending trends',
            'See the fraud detection engine in action',
            'Impersonate any demo user to explore different roles',
          ].map((feature) => (
            <div key={feature} className="flex items-center gap-2 text-sm text-indigo-800">
              <CheckCircle className="w-4 h-4 text-indigo-500 flex-shrink-0" />
              {feature}
            </div>
          ))}
        </div>

        <button
          onClick={handleExplore}
          disabled={exploring}
          className="w-full py-3 px-6 rounded-lg font-semibold text-white text-base transition-opacity mb-3 flex items-center justify-center gap-2 disabled:opacity-60"
          style={{ backgroundColor: 'var(--color-primary, #4f46e5)' }}
        >
          {exploring ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Opening…
            </>
          ) : (
            'Explore →'
          )}
        </button>
      </div>
    </div>
  );
};
