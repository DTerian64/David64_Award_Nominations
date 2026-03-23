// i18n must be imported before React so the instance is ready before any
// component calls useTranslation().
import './i18n';

import React from "react";
import ReactDOM from "react-dom/client";
import { MsalProvider } from "@azure/msal-react";
import App from "./App";
import "./index.css";
import { msalInstance } from "./msalInstance";
import { apiTokenRequest, loginRequest, apiConfig } from "./authConfig";
import { ImpersonationProvider } from "./contexts/ImpersonationContext";
import { TenantConfigProvider } from "./contexts/TenantConfigContext";

// MSAL v4 requires explicit initialization before any auth operations.
// Awaiting here (top-level await, valid in ES modules) ensures:
//   1. If this page loaded inside a popup after auth redirect, initialize()
//      detects the popup context, sends the result to the parent, and closes
//      the window — before React mounts and the user sees anything.
//   2. On a normal page load, the cache is fully hydrated before React renders,
//      so AuthenticatedTemplate gets the right state on the first paint.
await msalInstance.initialize();

// ── Pre-flight domain guard ────────────────────────────────────────────────
//
// Two scenarios handled here, BEFORE React mounts:
//
// Scenario A — Returning user with a cached session on the wrong domain:
//   The user visited sandbox-awards before, authenticated, and now has a
//   cached token in sessionStorage.  We silently acquire a token, call the
//   domain-redirect endpoint, and redirect immediately if there's a mismatch.
//   The user never sees the wrong-domain UI.
//
// Scenario B — Arriving via a domain-redirect from TenantConfigContext:
//   TenantConfigContext detected a mismatch after sign-in and redirected the
//   user here with ?login_hint=<upn>.  We use MSAL's ssoSilent() to silently
//   re-authenticate using the existing Azure AD session cookie — no second
//   sign-in prompt.  Falls back to loginRedirect with the hint pre-filled
//   if the silent attempt fails (e.g. the AD session has expired).
//
// Localhost is always exempt so local development works without Domain
// entries in the database.

const _currentHost = window.location.hostname;
const _isLocalDev  = _currentHost === "localhost" || _currentHost === "127.0.0.1";
const _API_BASE    = apiConfig.apiEndpoint;

// Restore active account now that the cache is loaded.
const accounts = msalInstance.getAllAccounts();
if (accounts.length > 0) {
  msalInstance.setActiveAccount(accounts[0]);
}

if (!_isLocalDev) {
  // ── Scenario A: pre-flight for cached session ──────────────────────────
  if (accounts.length > 0) {
    try {
      const tokenResp = await msalInstance.acquireTokenSilent({
        ...apiTokenRequest,
        account: accounts[0],
      });
      const pfResp = await fetch(
        `${_API_BASE}/api/public/domain-redirect?host=${encodeURIComponent(_currentHost)}`,
        { headers: { Authorization: `Bearer ${tokenResp.accessToken}` } },
      );
      if (pfResp.ok) {
        const { canonical_domain } = await pfResp.json() as { canonical_domain: string | null };
        if (canonical_domain && canonical_domain !== _currentHost) {
          // Pass the UPN so the target domain can use ssoSilent (Scenario B).
          const hint = accounts[0].username
            ? `?login_hint=${encodeURIComponent(accounts[0].username)}`
            : "";
          console.warn(
            `[DomainGuard] Cached session on wrong domain — ` +
            `current: ${_currentHost}, canonical: ${canonical_domain}. Redirecting.`,
          );
          window.location.replace(`https://${canonical_domain}${hint}`);
          // Execution stops — page is about to unload.
        }
      }
    } catch {
      // Silent token acquisition or pre-flight fetch failed — proceed
      // normally.  TenantConfigContext will catch the mismatch post-login.
    }
  }

  // ── Scenario B: arriving with ?login_hint after domain redirect ─────────
  const _searchParams = new URLSearchParams(window.location.search);
  const _loginHint    = _searchParams.get("login_hint");

  if (_loginHint && accounts.length === 0) {
    try {
      await msalInstance.ssoSilent({
        ...loginRequest,
        loginHint: _loginHint,
      });
      // ssoSilent succeeded — restore the newly cached account.
      const silentAccounts = msalInstance.getAllAccounts();
      if (silentAccounts.length > 0) {
        msalInstance.setActiveAccount(silentAccounts[0]);
      }
    } catch {
      // Azure AD session has expired or hint doesn't match.  Fall through to
      // the normal loginRedirect — the hint is still passed so the UPN is
      // pre-filled in the sign-in dialog (one-click rather than a full
      // re-entry of credentials).
      try {
        await msalInstance.loginRedirect({
          ...loginRequest,
          loginHint: _loginHint,
        });
        // loginRedirect navigates away — execution stops here.
      } catch {
        // loginRedirect also failed (e.g. during unit tests or JSDOM).
        // Proceed and let the normal auth flow handle it.
      }
    }
    // Remove login_hint from the URL so it isn't bookmarked or shared.
    const _cleanUrl = new URL(window.location.href);
    _cleanUrl.searchParams.delete("login_hint");
    window.history.replaceState({}, "", _cleanUrl.toString());
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MsalProvider instance={msalInstance}>
      <ImpersonationProvider>
        {/*
          TenantConfigProvider wraps App so the correct locale/theme is
          applied before any text or colour is rendered.
          Authentication state is derived reactively inside TenantConfigProvider
          via useMsal() so the config fetch triggers as soon as MSAL completes
          the auth redirect — no page refresh needed.
        */}
        <TenantConfigProvider>
          <App />
        </TenantConfigProvider>
      </ImpersonationProvider>
    </MsalProvider>
  </React.StrictMode>
);
