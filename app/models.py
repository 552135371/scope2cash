from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    step: str
    model: str
    mode: Literal["live", "mock"]
    ms: int
    note: str = ""


class Milestone(BaseModel):
    title: str
    deliverables: List[str]
    est_hours: float
    weeks: float = 1


class Scope(BaseModel):
    project_title: str
    client_name: str = "Client"
    summary: str
    project_type: str = "web"
    milestones: List[Milestone]
    assumptions: List[str] = []
    risks: List[str] = []
    open_questions: List[str] = []


class ScopeRequest(BaseModel):
    requirement: str = Field(min_length=20)


class ScopeResponse(BaseModel):
    scope: Scope
    trace: List[TraceStep]


class Source(BaseModel):
    title: str
    url: str
    snippet: str = ""


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


class Quote(BaseModel):
    currency: str = "USD"
    hourly_rate: float
    total: float
    line_items: List[LineItem]
    terms: List[str]
    cover_message: str
    negotiation_tips: List[str] = []


class QuoteRequest(BaseModel):
    scope: Scope
    pricing: PricingReference
    hourly_rate: Optional[float] = None
    position: Literal["value", "market", "premium"] = "market"


class QuoteResponse(BaseModel):
    quote: Quote
    trace: List[TraceStep]


class Invoice(BaseModel):
    id: str
    number: str
    milestone: str
    amount: float
    currency: str
    due_date: str
    client_email: str
    status: Literal["DRAFT", "SENT", "PAID", "OVERDUE", "CANCELLED"] = "DRAFT"
    payer_link: str = ""
    days_overdue: int = 0
    provider: Literal["paypal-sandbox", "paypal-live", "mock"] = "mock"


class InvoiceDraftRequest(BaseModel):
    scope: Scope
    quote: Quote
    client_email: str


class ReminderResponse(BaseModel):
    invoice_id: str
    subject: str
    body: str
    tone: str
    trace: List[TraceStep]
