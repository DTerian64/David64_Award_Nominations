import { msalInstance } from "../msalInstance";
import { loginRequest, apiConfig } from '../authConfig';

export const getAccessToken = async (): Promise<string> => {
  const accounts = msalInstance.getAllAccounts();
  
  if (accounts.length === 0) {
    throw new Error('No accounts found. Please sign in.');
  }

  const request = {
    ...loginRequest,
    account: accounts[0],
  };

  try {
    const response = await msalInstance.acquireTokenSilent(request);
    return response.accessToken;
  } catch (error) {
    console.error('Silent token acquisition failed:', error);
    const response = await msalInstance.acquireTokenPopup(request);
    return response.accessToken;
  }
};

interface RequestOptions extends RequestInit {
  headers?: Record<string, string>;
}

export const apiCall = async <T = any>(
  endpoint: string,
  options: RequestOptions = {}
): Promise<T> => {
  const token = await getAccessToken();

  const response = await fetch(`${apiConfig.apiEndpoint}${endpoint}`, {
    ...options,
    headers: {
      ...options.headers,
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `API call failed: ${response.statusText}`);
  }

  return response.json();
};
