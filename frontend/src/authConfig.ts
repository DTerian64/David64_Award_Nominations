import type { Configuration } from "@azure/msal-browser";

/**
 * Vite environment variables MUST be prefixed with VITE_*
 * and are accessed via import.meta.env
 *
 * Required:
 *  - VITE_CLIENT_ID
 *  - VITE_TENANT_ID
 *  - VITE_API_CLIENT_ID
 *
 * Optional:
 *  - VITE_API_URL (default: http://127.0.0.1:8000)
 */

const clientId = import.meta.env.VITE_CLIENT_ID as string;
const tenantId = import.meta.env.VITE_TENANT_ID as string;
const apiScope = import.meta.env.VITE_API_SCOPE as string;

if (!clientId || !tenantId || !apiScope) {
  throw new Error("Missing required Vite auth environment variables.");
}
else {
  console.log("Loaded auth config from environment:");
  console.log("clientId:", clientId);
  console.log("tenantId:", tenantId);
  console.log("apiScope:", apiScope);
}

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin + "/", // trailing slash must match app registration
  },
  cache: {
    cacheLocation: "sessionStorage", // or "localStorage"
    storeAuthStateInCookie: false,
  },
};

/**
 * Scope for the FastAPI API — injected via VITE_API_SCOPE app setting (set by Terraform).
 * Format: api://<tenantId>/<app-slug>/access_as_user
 */
export const loginRequest = {
  scopes: [
    [apiScope],
    "openid",
    "profile",
    "email",
  ],
extraQueryParameters: {
    claims: JSON.stringify({
      id_token: {
        roles: null
      }
    })
  }
};

/**
 * Your API base URL (used only if you choose direct calls; with Vite proxy, you can ignore it)
 */
export const apiConfig = {
  apiEndpoint: (import.meta.env.VITE_API_URL as string) || "http://localhost:8000",
};
