import React from 'react';
import { useMsal } from '@azure/msal-react';
import { loginRequest } from '../authConfig';

export const SignInButton: React.FC = () => {
  const { instance } = useMsal();

  const handleLogin = () => {
    // loginRedirect navigates the main window to Microsoft's login page.
    // This avoids the COOP/BCG issue where login.microsoftonline.com sends
    // "Cross-Origin-Opener-Policy: same-origin", which severs the popup's
    // browsing context group and prevents window.close() from working,
    // leaving a blank popup open with the auth code in the URL.
    // The redirect response (#code=...) is processed by msalInstance.initialize()
    // in main.tsx before React mounts, so the authenticated state is available
    // on the very first render after the redirect lands.
    instance.loginRedirect(loginRequest).catch((error) => {
      console.error('Login error:', error);
    });
  };

  return (
    <button onClick={handleLogin} className="btn-primary">
      Sign in with Microsoft
    </button>
  );
};