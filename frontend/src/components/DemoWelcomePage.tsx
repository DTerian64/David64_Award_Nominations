/**
 * DemoWelcomePage.tsx
 *
 * Landing page at /demo/welcome — shown after a visitor accepts the
 * Microsoft B2B invitation email (inviteRedirectUrl points here).
 *
 * The user is already authenticated with Microsoft at this point.
 * We trigger loginRedirect so MSAL processes their session and they
 * land on the main app dashboard.
 */

import React, { useEffect, useState } from 'react';
import { useMsal } from '@azure/msal-react';
import { Award, CheckCircle } from 'lucide-react';
import { loginRequest } from '../authConfig';

const DEMO_AAD_TENANT_ID = import.meta.env.VITE_DEMO_AAD_TENANT_ID as string | undefined;

export const DemoWelcomePage: React.FC = () => {
  const { instance, accounts } = useMsal();
  const [countdown, setCountdown] = useState(5);

  // If already authenticated (e.g. returning visitor), redirect immediately
  useEffect(() => {
    if (accounts.length > 0) {
      window.location.href = '/';
      return;
    }

    // Countdown then auto-trigger sign-in
    const timer = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          clearInterval(timer);
          handleSignIn();
          return 0;
        }
        return c - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  const handleSignIn = () => {
    const request = DEMO_AAD_TENANT_ID
      ? {
          ...loginRequest,
          authority: `https://login.microsoftonline.com/${DEMO_AAD_TENANT_ID}`,
        }
      : loginRequest;

    instance.loginRedirect(request).catch((err) => {
      console.error('MSAL redirect error:', err);
    });
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
          onClick={handleSignIn}
          className="w-full py-3 px-6 rounded-lg font-semibold text-white text-base transition-colors mb-3"
          style={{ backgroundColor: 'var(--color-primary, #4f46e5)' }}
        >
          Sign In & Explore →
        </button>

        <p className="text-xs text-gray-400">
          Signing in automatically in {countdown}s…
        </p>
      </div>
    </div>
  );
};
