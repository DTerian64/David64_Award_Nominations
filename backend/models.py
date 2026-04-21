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
    Amount: int = Field(gt=0)
    NominationDescription: str = Field(min_length=1, max_length=500)


class Nomination(BaseModel):
    NominationId: int
    NominatorId: int
    BeneficiaryId: int
    ApproverId: int
    Amount: int
    Currency: str
    NominationDescription: str
    NominationDate: datetime
    ApprovedDate: Optional[datetime] = None
    PayedDate: Optional[datetime] = None
    Status: Literal["Pending", "Approved", "Paid", "Rejected"]
    # Business lifecycle data — when did the approver first receive the nomination
    # request? Populated by the auxiliary worker after SMTP hand-off.
    # None = approver not yet notified (event in flight or worker pending).
    ApproverNotifiedAt: Optional[datetime] = None


class NominationApproval(BaseModel):
    NominationId: int
    Approved: bool
    Comments: Optional[str] = None


class ProcessedEvent(BaseModel):
    """Represents a row in dbo.ProcessedEvents — the Service Bus idempotency log.
    Used by the auxiliary worker to record every processed message and prevent
    duplicate handling on redelivery (at-least-once delivery guarantee).
    """
    MessageId:    str               # Service Bus message ID (GUID string, PK)
    EventType:    str               # e.g. 'nomination.created', 'nomination.approved'
    NominationId: Optional[int] = None  # linked nomination, if applicable
    ProcessedAt:  datetime          # UTC timestamp written at insert time
    Result:       Literal["success", "skipped", "error"]


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


# ============================================================================
# ANALYTICS MODELS
# ============================================================================

class SpendingTrendPoint(BaseModel):
    """Data point for spending trend over time"""
    date: date
    amount: float
    nominationCount: int


class DepartmentSpending(BaseModel):
    """Department-level spending metrics"""
    departmentName: str
    totalSpent: float
    nominationCount: int
    averageAmount: float


class TopRecipient(BaseModel):
    """Top recipients by count or amount"""
    UserId: int
    FirstName: str
    LastName: str
    nominationCount: int
    totalAmount: float


class FraudAlert(BaseModel):
    """Fraud detection alert"""
    NominationId: int
    riskLevel: str
    fraudScore: int
    flags: List[str]
    nominatorName: str
    beneficiaryName: str
    amount: float
    nominationDate: date


class AnalyticsOverview(BaseModel):
    """High-level analytics metrics"""
    totalNominationsAllTime: int
    totalAmountSpent: float
    approvedNominations: int
    pendingNominations: int
    averageAwardAmount: float
    averageTimeToApprovalDays: float
    rejectionRate: float
    fraudAlertsThisMonth: int
    departmentCount: int


class BudgetMetrics(BaseModel):
    """Budget allocation and utilization"""
    departmentName: str
    allocated: float
    spent: float
    remainingBudget: float
    utilizationPercent: float
    forecastedTotal: float


class DiversityMetrics(BaseModel):
    """Diversity in award distribution"""
    uniqueRecipients: int
    totalNominations: int
    averageNominationsPerRecipient: float
    giniCoefficient: float  # 0 = perfect equality, 1 = perfect inequality
    topRecipientPercent: float  # % of awards going to top 10%
