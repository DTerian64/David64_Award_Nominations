import { msalInstance } from "../msalInstance";
import { apiTokenRequest, apiConfig } from '../authConfig';

export const getAccessToken = async (): Promise<string> => {
  const accounts = msalInstance.getAllAccounts();

  if (accounts.length === 0) {
    throw new Error('No accounts found. Please sign in.');
  }

  const request = {
    ...apiTokenRequest,    // only the API scope — ensures aud = api://<CLIENT_ID>
    account: accounts[0],
  };

  try {
    const response = await msalInstance.acquireTokenSilent(request);
    return response.accessToken;
  } catch (error) {
    // Silent acquisition failed (expired, no cache, consent needed, etc.).
    // Use redirect rather than popup to avoid the same COOP issue as login.
    console.error('Silent token acquisition failed, falling back to redirect:', error);
    await msalInstance.acquireTokenRedirect(request);
    // acquireTokenRedirect navigates away — execution stops here.
    // The redirect will return to main.tsx where initialize() processes it.
    throw new Error('Redirecting for token acquisition...');
  }
};

interface RequestOptions extends RequestInit {
  headers?: Record<string, string>;
}

/**
 * Enhanced API call function with impersonation support
 * If impersonatedUserUPN is provided, it will be sent in the X-Impersonate-User header
 */
export const apiCall = async <T = any>(
  endpoint: string,
  options: RequestOptions = {},
  impersonatedUserUPN?: string
): Promise<T> => {
  const token = await getAccessToken();

  const headers: Record<string, string> = {
    ...options.headers,
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };

  // Add impersonation header if provided
  if (impersonatedUserUPN) {
    headers['X-Impersonate-User'] = impersonatedUserUPN;
  }

  const response = await fetch(`${apiConfig.apiEndpoint}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `API call failed: ${response.statusText}`);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
};
