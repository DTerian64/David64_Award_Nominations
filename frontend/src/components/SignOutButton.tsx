import React from 'react';
import { useMsal } from '@azure/msal-react';

export const SignOutButton: React.FC = () => {
  const { instance } = useMsal();

  const handleLogout = async () => {
    try {
      await instance.logoutPopup({
        mainWindowRedirectUri: '/',
      });
    } catch (error) {
      console.error('Logout error:', error);
    }
  };

  return (
    <button onClick={handleLogout} className="btn-secondary">
      Sign Out
    </button>
  );
};