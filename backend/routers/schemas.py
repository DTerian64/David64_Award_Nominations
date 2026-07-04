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
    NominationDescription: str = Field(min_length=10, max_length=500)
    # Optional — only submitted when the tenant has nomination_categories rows.
    # The backend validates that it is provided when categories exist.
    CategoryId: Optional[int] = None


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
    Status: Literal["Submitted", "Pending", "PendingHRBPReview", "Approved", "Paid", "Rejected"]
    CategoryDescription: Optional[str] = None
    # Business lifecycle data — when did the approver first receive the nomination
    # request? Populated by the auxiliary worker after SMTP hand-off.
    # None = approver not yet notified (event in flight or worker pending).
    ApproverNotifiedAt: Optional[datetime] = None
    # Rejection metadata — populated only when Status == 'Rejected'
    RejectionReason: Optional[str] = None
    RejectionActor:  Optional[str] = None


class NominationApproval(BaseModel):
    NominationId: int
    Approved: bool
    reason: str = ""   # rejection reason; ignored when Approved=True


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


class CertificateResponse(BaseModel):
    """Award certificate link returned by the certificate endpoint.

    DownloadUrl is a short-lived, read-only SAS URL to the PDF in blob storage —
    the PDF bytes never stream through the API. Cached is True when an existing
    certificate was reused rather than freshly generated.
    """
    DownloadUrl: str
    Cached: bool


class HealthResponse(BaseModel):
    status: str


# ── Payroll pay-lookup ─────────────────────────────────────────────────────────

class PayrollEntry(BaseModel):
    payroll_uuid:     str
    payroll_type:     Literal["regular", "off_cycle"]
    pay_period_start: str   # YYYY-MM-DD
    pay_period_end:   str   # YYYY-MM-DD
    check_date:       Optional[str] = None  # YYYY-MM-DD
    gross_pay:        float
    net_pay:          float
    total_deductions: float


class EmployeeAddress(BaseModel):
    street_1: str = ""
    street_2: str = ""
    city:     str = ""
    state:    str = ""
    zip:      str = ""


class EmployeePayrate(BaseModel):
    rate:         str = ""   # e.g. "90000.00"
    payment_unit: str = ""   # "Hour" | "Week" | "Month" | "Year"


class EmployeeProfile(BaseModel):
    employee_uuid: str
    full_name:     str
    work_email:    str
    address:       EmployeeAddress
    payrate:       EmployeePayrate


class EmployeePayResponse(BaseModel):
    upn:     str
    year:    int
    month:   int
    profile: Optional[EmployeeProfile] = None
    entries: List[PayrollEntry]


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
