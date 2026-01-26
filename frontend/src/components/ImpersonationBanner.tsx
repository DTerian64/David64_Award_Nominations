import React from 'react';
import { AlertCircle, X } from 'lucide-react';
import { useImpersonation } from '../contexts/ImpersonationContext';
import { useMsal } from '@azure/msal-react';

export const ImpersonationBanner: React.FC = () => {
  const { impersonatedUser, isImpersonating, stopImpersonation } = useImpersonation();
  const { accounts } = useMsal();

  if (!isImpersonating || !impersonatedUser) return null;

  return (
    <div className="bg-purple-600 text-white py-3 px-4 shadow-lg">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <AlertCircle className="w-5 h-5 flex-shrink-0" />
          <div className="text-sm">
            <span className="font-semibold">Impersonation Mode:</span> Viewing as{' '}
            <span className="font-bold">
              {impersonatedUser.FirstName} {impersonatedUser.LastName}
            </span>{' '}
            ({impersonatedUser.userPrincipalName})
            <span className="mx-2">•</span>
            <span className="text-purple-200">
              Your admin account: {accounts[0]?.username}
            </span>
          </div>
        </div>
        <button
          onClick={stopImpersonation}
          className="flex items-center space-x-2 px-4 py-1.5 bg-white text-purple-600 rounded-md hover:bg-purple-50 transition-colors font-semibold text-sm"
        >
          <X className="w-4 h-4" />
          <span>Stop Impersonating</span>
        </button>
      </div>
    </div>
  );
};