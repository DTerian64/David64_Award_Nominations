// i18n must be imported before React so the instance is ready before any
// component calls useTranslation().
import './i18n';

// Initialise Azure Application Insights before React mounts so the first
// page-view and any early exceptions are captured.
import './appInsights';

import React from "react";
import ReactDOM from "react-dom/client";
import { MsalProvider } from "@azure/msal-react";
import App from "./App";
import "./index.css";
import { msalInstance } from "./msalInstance";
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

// Restore active account now that the cache is loaded.
const accounts = msalInstance.getAllAccounts();
if (accounts.length > 0) {
  msalInstance.setActiveAccount(accounts[0]);
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
