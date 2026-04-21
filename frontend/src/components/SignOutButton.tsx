import { useMsal } from '@azure/msal-react';
import { useTranslation } from 'react-i18next';

export const SignOutButton = () => {
  const { instance } = useMsal();
  const { t } = useTranslation();

  const handleLogout = () => {
    instance.logoutRedirect({
      postLogoutRedirectUri: window.location.origin,
    });
  };

  return (
    <button onClick={handleLogout} className="px-4 py-2 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 font-medium text-sm transition-colors">
      {t('auth.signOut')}
    </button>
  );
};