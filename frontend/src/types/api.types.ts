export interface User {
  UserId: number;
  userPrincipalName: string;
  FirstName: string;
  LastName: string;
  Title: string;
  ManagerId: number | null;
}

export interface NominationCreate {
  BeneficiaryId: number;
  DollarAmount: number;
  NominationDescription: string;
}

export interface Nomination {
  NominationId: number;
  NominatorId: number;
  BeneficiaryId: number;
  ApproverId: number;
  DollarAmount: number;
  NominationDescription: string;
  NominationDate: string;
  ApprovedDate: string | null;
  PayedDate: string | null;
  Status: 'Pending' | 'Approved' | 'Paid' | 'Rejected';
}

export interface NominationApproval {
  NominationId: number;
  Approved: boolean;
  Comments?: string;
}
