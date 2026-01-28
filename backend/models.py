from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import date, datetime


class User(BaseModel):
    UserId: int
    userPrincipalName: str
    FirstName: str
    LastName: str
    Title: str
    ManagerId: Optional[int] = None


class NominationCreate(BaseModel):
    BeneficiaryId: int
    DollarAmount: int = Field(gt=0)
    NominationDescription: str = Field(min_length=1, max_length=500)


class Nomination(BaseModel):
    NominationId: int
    NominatorId: int
    BeneficiaryId: int
    ApproverId: int
    DollarAmount: int
    NominationDescription: str
    NominationDate: date
    ApprovedDate: Optional[datetime] = None
    PayedDate: Optional[datetime] = None
    Status: Literal["Pending", "Approved", "Paid", "Rejected"]


class NominationApproval(BaseModel):
    NominationId: int
    Approved: bool
    Comments: Optional[str] = None


class StatusResponse(BaseModel):
    Status: str
    Message: str


class HealthResponse(BaseModel):
    status: str


class AuditLog(BaseModel):
    """Audit log entry for impersonation tracking"""
    AuditId: int
    Timestamp: datetime
    AdminUPN: str
    ImpersonatedUPN: str
    Action: str
    Details: Optional[str] = None
    IpAddress: Optional[str] = None
