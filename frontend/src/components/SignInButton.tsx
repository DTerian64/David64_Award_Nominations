import React from 'react';
import { useMsal } from '@azure/msal-react';
import { loginRequest } from '../authConfig';

export const SignInButton: React.FC = () => {
  const { instance } = useMsal();

  const handleLogin = async () => {
    try {
      const result = await instance.loginPopup(loginRequest);
      // REQUIRED: set the active account so AuthenticatedTemplate flips immediately.
      // loginPopup resolves with the account but MSAL doesn't auto-activate it.
      instance.setActiveAccount(result.account);
    } catch (error) {
      console.error('Login error:', error);
    }
  };

  return (
    <button onClick={handleLogin} className="btn-primary">
      Sign in with Microsoft
    </button>
  );
};