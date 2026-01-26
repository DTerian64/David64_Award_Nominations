import React, { createContext, useContext, useState, useEffect} from 'react';
import type { ReactNode } from 'react';

import { useMsal } from '@azure/msal-react';

interface ImpersonationContextType {
  impersonatedUser: ImpersonatedUser | null;
  isImpersonating: boolean;
  isAdmin: boolean;
  startImpersonation: (user: ImpersonatedUser) => void;
  stopImpersonation: () => void;
  getEffectiveUser: () => string; // Returns UPN of current or impersonated user
}

interface ImpersonatedUser {
  UserId: number;
  userPrincipalName: string;
  FirstName: string;
  LastName: string;
}

const ImpersonationContext = createContext<ImpersonationContextType | undefined>(undefined);

export const useImpersonation = () => {
  const context = useContext(ImpersonationContext);
  if (!context) {
    throw new Error('useImpersonation must be used within ImpersonationProvider');
  }
  return context;
};

interface ImpersonationProviderProps {
  children: ReactNode;
}

export const ImpersonationProvider: React.FC<ImpersonationProviderProps> = ({ children }) => {
  const { accounts } = useMsal();
  const [impersonatedUser, setImpersonatedUser] = useState<ImpersonatedUser | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);

  // Check if current user is admin based on Azure AD roles
  useEffect(() => {
    if (accounts.length > 0) {
      const roles = accounts[0]?.idTokenClaims?.roles as string[] | undefined;
      setIsAdmin(roles?.includes('AWard_Nomination_Admin') || roles?.includes('Administrator') || false);
    }
  }, [accounts]);

  // Persist impersonation state in sessionStorage
  useEffect(() => {
    const stored = sessionStorage.getItem('impersonatedUser');
    if (stored) {
      try {
        setImpersonatedUser(JSON.parse(stored));
      } catch (e) {
        sessionStorage.removeItem('impersonatedUser');
      }
    }
  }, []);

  const startImpersonation = (user: ImpersonatedUser) => {
    if (!isAdmin) {
      console.error('Only admins can impersonate users');
      return;
    }
    
    setImpersonatedUser(user);
    sessionStorage.setItem('impersonatedUser', JSON.stringify(user));
    
    // Log the impersonation action
    console.log(`Admin ${accounts[0]?.username} started impersonating ${user.userPrincipalName}`);
  };

  const stopImpersonation = () => {
    console.log(`Admin ${accounts[0]?.username} stopped impersonating ${impersonatedUser?.userPrincipalName}`);
    setImpersonatedUser(null);
    sessionStorage.removeItem('impersonatedUser');
  };

  const getEffectiveUser = (): string => {
    return impersonatedUser?.userPrincipalName || accounts[0]?.username || '';
  };

  return (
    <ImpersonationContext.Provider
      value={{
        impersonatedUser,
        isImpersonating: !!impersonatedUser,
        isAdmin,
        startImpersonation,
        stopImpersonation,
        getEffectiveUser,
      }}
    >
      {children}
    </ImpersonationContext.Provider>
  );
};