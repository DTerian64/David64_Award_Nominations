import React, { createContext, useContext, useState, useEffect} from 'react';
import type { ReactNode } from 'react';

import { useMsal } from '@azure/msal-react';
import { msalInstance } from '../msalInstance';
import { apiTokenRequest } from '../authConfig';

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

  // Check admin role by acquiring an access token and decoding its `roles` claim.
  // Reading from idTokenClaims is unreliable: the ID token may be stale (freshly
  // assigned roles won't appear until a new ID token is issued), and the useMsal()
  // `accounts` array can be empty on first render even when the user IS signed in.
  // The access token is what the backend actually validates, so it's the ground truth.
  useEffect(() => {
    const checkAdminRole = async () => {
      try {
        // Use msalInstance directly (same path as getAccessToken in api.ts) to
        // avoid depending on the useMsal() accounts array which may be empty.
        const allAccounts = msalInstance.getAllAccounts();
        if (allAccounts.length === 0) {
          setIsAdmin(false);
          return;
        }

        const response = await msalInstance.acquireTokenSilent({
          ...apiTokenRequest,
          account: allAccounts[0],
        });

        // JWT is three base64url segments separated by '.'.  The middle one is the payload.
        const payloadB64 = response.accessToken.split('.')[1];
        const payload = JSON.parse(atob(payloadB64.replace(/-/g, '+').replace(/_/g, '/')));
        const roles = payload.roles as string[] | undefined;
        setIsAdmin(
          roles?.includes('AWard_Nomination_Admin') ||
          roles?.includes('Administrator') ||
          false
        );
      } catch (err) {
        console.warn('ImpersonationContext: could not acquire access token to check roles, falling back to idTokenClaims:', err);
        // Fallback: read from the MSAL account's cached ID token claims
        const allAccounts = msalInstance.getAllAccounts();
        if (allAccounts.length > 0) {
          const roles = allAccounts[0]?.idTokenClaims?.roles as string[] | undefined;
          setIsAdmin(roles?.includes('AWard_Nomination_Admin') || roles?.includes('Administrator') || false);
        } else {
          setIsAdmin(false);
        }
      }
    };

    checkAdminRole();
  }, [accounts]); // re-run whenever useMsal() detects an account change (e.g. after sign-in)

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