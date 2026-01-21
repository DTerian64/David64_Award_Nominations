import { apiCall } from './api';
import type { User, Nomination, NominationCreate, NominationApproval } from '../types/api.types';

export const getUsers = (): Promise<User[]> => 
  apiCall<User[]>('/api/users');

export const createNomination = (nomination: NominationCreate): Promise<void> =>
  apiCall('/api/nominations', {
    method: 'POST',
    body: JSON.stringify(nomination),
  });

export const getPendingNominations = (): Promise<Nomination[]> =>
  apiCall<Nomination[]>('/api/nominations/pending');

export const getNominationHistory = (): Promise<Nomination[]> =>
  apiCall<Nomination[]>('/api/nominations/history');

export const approveNomination = (
  nominationId: number,
  approved: boolean,
  comments?: string
): Promise<void> =>
  apiCall('/api/nominations/approve', {
    method: 'POST',
    body: JSON.stringify({
      NominationId: nominationId,
      Approved: approved,
      Comments: comments,
    } as NominationApproval),
  });
