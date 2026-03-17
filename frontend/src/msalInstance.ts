import { PublicClientApplication } from "@azure/msal-browser";
import { msalConfig } from "./authConfig";

export const msalInstance = new PublicClientApplication(msalConfig);

// Restore active account on page load/refresh.
// MSAL rehydrates accounts from sessionStorage but doesn't auto-activate one —
// without this, AuthenticatedTemplate stays hidden after a page refresh.
const accounts = msalInstance.getAllAccounts();
if (accounts.length > 0) {
  msalInstance.setActiveAccount(accounts[0]);
}
