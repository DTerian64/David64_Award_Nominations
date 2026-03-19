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
 * Contains ONLY OIDC scopes. The API scope is intentionally NOT included here.
 *
 * Why: if apiScope is mixed in here alongside openid/profile/email, MSAL caches
 * the resulting access token under a combined scope key.  When acquireTokenSilent
 * later asks for [apiScope] alone it can't find that cache entry and may fall
 * back to returning the cached ID token in accessToken — causing a 401 because
 * the backend expects aud=api://<CLIENT_ID> but gets aud=<FRONTEND_CLIENT_ID>.
 *
 * By keeping loginRequest OIDC-only, acquireTokenSilent({scopes:[apiScope]})
 * always performs a clean refresh-token exchange and gets a properly scoped
 * access token (aud = api://<CLIENT_ID>, scp = access_as_user).
 *
 * The `claims` parameter requests app roles in the ID token (for role-based UI).
 */
export const loginRequest = {
  scopes: [
    apiScope,       // included so Azure AD grants consent and caches the API
    "openid",       // access token during the initial login round-trip.
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
