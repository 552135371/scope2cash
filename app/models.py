import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

PROJECT_TYPES = ("ios", "mobile", "web", "automation", "design", "data")
Mode = Literal["live", "mock", "fallback", "error"]


def _num(v, default: float) -> float:
    """Models sometimes return '10-12' or '12h'; take the mean of the numbers present."""
    if isinstance(v, (int, float)):
        return float(v)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(v or ""))]
    return sum(nums[:2]) / len(nums[:2]) if nums else default


def _strs(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    out = []
    for x in v:
        if isinstance(x, dict):
            x = x.get("text") or x.get("title") or x.get("question") or x.get("description") or " ".join(map(str, x.values()))
        out.append(str(x))
    return out


class TraceStep(BaseModel):
    step: str
    model: str
    mode: Mode
    ms: int
    tier: str = "tool"  # fast | main | deep | tool
    note: str = ""
    warning: str = ""
    tokens: int = 0


class Milestone(BaseModel):
    title: str
    deliverables: List[str]
    est_hours: float
    weeks: float = 1

    @field_validator("est_hours", mode="before")
    @classmethod
    def _h(cls, v):
        return max(1.0, min(_num(v, 8), 400.0))

    @field_validator("weeks", mode="before")
    @classmethod
    def _w(cls, v):
        return max(0.5, min(_num(v, 1), 26.0))

    @field_validator("deliverables", mode="before")
    @classmethod
    def _d(cls, v):
        return _strs(v)


class Scope(BaseModel):
    project_title: str
    client_name: str = "Client"
    summary: str
    project_type: str = "web"
    milestones: List[Milestone] = Field(min_length=1)
    assumptions: List[str] = []
    risks: List[str] = []
    open_questions: List[str] = []
    confidence: float = 0.7  # how complete the brief is (0-1); low -> ask before quoting
    client_budget: Optional[float] = None
    deadline: Optional[str] = None
    language: str = "en"
    contact_name: Optional[str] = None

    @field_validator("project_type", mode="before")
    @classmethod
    def _pt(cls, v):
        v = str(v or "web").lower().strip()
        aliases = {"android": "mobile", "flutter": "mobile", "react native": "mobile", "website": "web", "saas": "web",
                   "n8n": "automation", "zapier": "automation", "branding": "design", "logo": "design", "scraping": "data"}
        return v if v in PROJECT_TYPES else aliases.get(v, "web")

    @field_validator("assumptions", "risks", "open_questions", mode="before")
    @classmethod
    def _l(cls, v):
        return _strs(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _c(cls, v):
        x = _num(v, 0.7)
        return max(0.0, min(x / 100 if x > 1 else x, 1.0))

    @field_validator("client_budget", mode="before")
    @classmethod
    def _b(cls, v):
        if v in (None, "", 0, "null", "unknown"):
            return None
        x = _num(v, 0)
        return x or None


class ScopeRequest(BaseModel):
    requirement: str = Field(min_length=20, max_length=20000)


class ScopeResponse(BaseModel):
    scope: Scope
    trace: List[TraceStep]


class Source(BaseModel):
    title: str
    url: str
    snippet: str = ""
    score: Optional[float] = None


class PricingReference(BaseModel):
    currency: str = "USD"
    low: float
    median: float
    high: float
    hourly_low: float
    hourly_high: float
    rationale: str
    sources: List[Source] = []
    mock: bool = False
    grounded: bool = False  # True when the hourly range is backed by figures found in the cited sources
    evidence: List[str] = []  # the dollar figures we found in the sources
    queries: List[str] = []


class PricingRequest(BaseModel):
    scope: Scope


class PricingResponse(BaseModel):
    pricing: PricingReference
    trace: List[TraceStep]


class LineItem(BaseModel):
    milestone: str
    hours: float
    amount: float
    due_date: str
    percent: float
    deliverables: List[str] = []


class Quote(BaseModel):
    currency: str = "USD"
    hourly_rate: float
    total: float
    line_items: List[LineItem]
    terms: List[str]
    cover_message: str
    negotiation_tips: List[str] = []
    budget_note: str = ""


class QuoteRequest(BaseModel):
    scope: Scope
    pricing: PricingReference
    hourly_rate: Optional[float] = Field(default=None, gt=0, le=1000)
    position: Literal["value", "market", "premium"] = "market"


class QuoteResponse(BaseModel):
    quote: Quote
    trace: List[TraceStep]


InvoiceStatus = Literal["DRAFT", "SENT", "PARTIALLY_PAID", "PAID", "OVERDUE", "CANCELLED", "REFUNDED"]


class Invoice(BaseModel):
    id: str
    number: str
    milestone: str
    amount: float
    currency: str
    due_date: str
    client_email: str
    status: InvoiceStatus = "DRAFT"
    payer_link: str = ""
    days_overdue: int = 0
    provider: Literal["paypal-sandbox", "paypal-live", "mock"] = "mock"
    provider_status: str = ""  # raw PayPal status, e.g. UNPAID, PAYMENT_PENDING
    amount_due: Optional[float] = None
    approved_at: str = ""
    reminded_at: str = ""
    # Pipeline context (v0.3): which client/project the milestone belongs to, and when PayPal said it was paid.
    client_name: str = ""
    project: str = ""
    created_at: str = ""
    paid_at: str = ""
    last_event: str = ""  # last PayPal webhook event applied, e.g. INVOICING.INVOICE.PAID
    sample: bool = False  # seeded demo data (never a real client)
    # PayPal Orders v2 checkout link for this milestone (an alternative to the invoice's payer link)
    order_id: str = ""
    order_status: str = ""  # CREATED | PAYER_ACTION_REQUIRED | APPROVED | COMPLETED | ...
    checkout_link: str = ""
    capture_id: str = ""


class InvoiceDraftRequest(BaseModel):
    scope: Scope
    quote: Quote
    client_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvoiceDraftResponse(BaseModel):
    invoices: List[Invoice]
    trace: List[TraceStep]


class ReminderResponse(BaseModel):
    invoice_id: str
    subject: str
    body: str
    tone: str
    trace: List[TraceStep]
    provider_note: str = ""


class ReminderSendResponse(BaseModel):
    invoice: Invoice
    trace: List[TraceStep]


class WebhookResult(BaseModel):
    status: Literal["processed", "duplicate", "ignored"]
    event_id: str
    event_type: str
    invoice_id: str = ""
    invoice_status: str = ""
    verified_by: str = ""  # paypal-api | mock-hmac
    detail: str = ""


class AgentRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)


class ToolCallRecord(BaseModel):
    tool: str
    args: dict = {}
    kind: Literal["read", "write"] = "read"
    mode: Mode = "mock"
    ms: int = 0
    summary: str = ""


class AgentAction(BaseModel):
    id: str
    tool: str
    params: dict
    invoice_id: str = ""
    invoice_number: str = ""
    summary: str = ""
    reason: str = ""
    status: Literal["pending", "executed", "rejected", "failed"] = "pending"
    result: str = ""
    created_at: str = ""
    decided_at: str = ""


class AgentResponse(BaseModel):
    answer: str
    tool_calls: List[ToolCallRecord]
    proposals: List[AgentAction]
    trace: List[TraceStep]
    toolkit: str = ""
