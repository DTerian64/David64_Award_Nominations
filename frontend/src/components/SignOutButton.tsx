//import React from 'react';
import { useMsal } from '@azure/msal-react';

export const SignOutButton = () => {
  const { instance } = useMsal();

   const handleLogout = () => {
    instance.logoutRedirect({
      postLogoutRedirectUri: window.location.origin,
    });
  };

  return (
    <button onClick={handleLogout} className="btn-secondary">
      Sign Out
    </button>
  );
};