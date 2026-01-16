from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import date


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
    NominationDescription: str = Field(min_length=1, max_length=2000)


class Nomination(BaseModel):
    NominationId: int
    NominatorId: int
    BeneficiaryId: int
    ApproverId: int
    DollarAmount: int
    NominationDescription: str
    NominationDate: date
    ApprovedDate: Optional[date] = None
    PayedDate: Optional[date] = None
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