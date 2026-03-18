import type { Configuration } from "@azure/msal-browser";

/**
 * Vite environment variables MUST be prefixed with VITE_*
 * and are accessed via import.meta.env
 *
 * Required:
 *  - VITE_CLIENT_ID     — SPA app registration client ID
 *  - VITE_API_SCOPE     — API scope URI (e.g. api://<api-client-id>/access_as_user)
 *
 * Optional:
 *  - VITE_API_URL (default: http://127.0.0.1:8000)
 */

const clientId = import.meta.env.VITE_CLIENT_ID as string;
const apiScope = import.meta.env.VITE_API_SCOPE as string;

if (!clientId || !apiScope) {
  throw new Error("Missing required Vite auth environment variables.");
}
else {
  console.log("Loaded auth config from environment:");
  console.log("clientId:", clientId);
  console.log("apiScope:", apiScope);
}

export const msalConfig: Configuration = {
  auth: {
    clientId,
    // /organizations accepts work & school accounts from ANY Azure AD tenant.
    // Do NOT use a specific tenant ID here — that would restrict login to a
    // single tenant and break multi-tenant sign-in for external organisations.
    authority: "https://login.microsoftonline.com/organizations",
    redirectUri: window.location.origin + "/", // trailing slash must match app registration
  },
  cache: {
    cacheLocation: "sessionStorage", // or "localStorage"
    storeAuthStateInCookie: false,
  },
};

/**
 * loginRequest — used for the initial interactive sign-in only.
 * Includes OIDC scopes so Azure AD returns an ID token with user profile claims,
 * plus the API scope so an access token is also acquired in the same round-trip.
 * The `claims` parameter requests app roles in the ID token (for role-based UI).
 */
export const loginRequest = {
  scopes: [
    apiScope,
    "openid",
    "profile",
    "email",
  ],
  extraQueryParameters: {
    claims: JSON.stringify({
      id_token: {
        roles: null,
      },
    }),
  },
};

/**
 * apiTokenRequest — used by getAccessToken() in api.ts for every API call.
 * Contains ONLY the API scope so MSAL returns the correct access token
 * (aud = api://<CLIENT_ID>) rather than an ID token or OIDC token.
 *
 * Do NOT include openid/profile/email here — mixing OIDC scopes into a
 * silent token acquisition request causes MSAL to fall back to interactive
 * flows and can result in the wrong token type being returned.
 */
export const apiTokenRequest = {
  scopes: [apiScope],
};

/**
 * Your API base URL (used only if you choose direct calls; with Vite proxy, you can ignore it)
 */
export const apiConfig = {
  apiEndpoint: (import.meta.env.VITE_API_URL as string) || "http://localhost:8000",
};
