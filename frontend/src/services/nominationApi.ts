import { apiCall } from './api';
import type { User, Nomination, NominationCreate, NominationApproval } from '../types/api.types';

/**
 * All API functions now accept an optional impersonatedUserUPN parameter
 * that will be passed to the backend via the X-Impersonate-User header
 */

export const getUsers = (impersonatedUserUPN?: string): Promise<User[]> => 
  apiCall<User[]>('/api/users', {}, impersonatedUserUPN);

export const createNomination = (
  nomination: NominationCreate,
  impersonatedUserUPN?: string
): Promise<void> =>
  apiCall('/api/nominations', {
    method: 'POST',
    body: JSON.stringify(nomination),
  }, impersonatedUserUPN);

export const getPendingNominations = (impersonatedUserUPN?: string): Promise<Nomination[]> =>
  apiCall<Nomination[]>('/api/nominations/pending', {}, impersonatedUserUPN);

export const getNominationHistory = (impersonatedUserUPN?: string): Promise<Nomination[]> =>
  apiCall<Nomination[]>('/api/nominations/history', {}, impersonatedUserUPN);

export const approveNomination = (
  nominationId: number,
  approved: boolean,
  comments?: string,
  impersonatedUserUPN?: string
): Promise<void> =>
  apiCall('/api/nominations/approve', {
    method: 'POST',
    body: JSON.stringify({
      NominationId: nominationId,
      Approved: approved,
      Comments: comments,
    } as NominationApproval),
  }, impersonatedUserUPN);