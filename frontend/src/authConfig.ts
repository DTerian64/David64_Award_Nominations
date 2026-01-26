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
const apiClientId = import.meta.env.VITE_API_CLIENT_ID as string;

export const msalConfig: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    redirectUri: window.location.origin, // e.g., http://localhost:5173
  },
  cache: {
    cacheLocation: "sessionStorage", // or "localStorage"
    storeAuthStateInCookie: false,
  },
};

/**
 * Scope for your FastAPI API (Expose an API -> scope: access_as_user)
 * This must match the scope name you created in the BACKEND app registration.
 */
export const loginRequest = {
  scopes: [
    `api://${apiClientId}/access_as_user`,
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
