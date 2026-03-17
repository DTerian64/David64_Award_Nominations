import React from "react";
import ReactDOM from "react-dom/client";
import { MsalProvider } from "@azure/msal-react";
import App from "./App";
import "./index.css";
import { msalInstance } from "./msalInstance";
import { ImpersonationProvider } from "./contexts/ImpersonationContext";

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
        <App />
      </ImpersonationProvider>
    </MsalProvider>
  </React.StrictMode>
);
