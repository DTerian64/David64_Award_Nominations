import { PublicClientApplication } from "@azure/msal-browser";
import { msalConfig } from "./authConfig";

// Create the instance only — do NOT call initialize() or getAllAccounts() here.
// In MSAL v4 the cache is not loaded until initialize() completes.
// Initialization and account restoration happen in main.tsx before React mounts.
export const msalInstance = new PublicClientApplication(msalConfig);
