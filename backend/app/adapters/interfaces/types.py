"""Pydantic models for adapter I/O — shared across all adapter implementations."""

from datetime import date, datetime

from pydantic import BaseModel

# ── CRM ───────────────────────────────────────────────────────────────

class Account(BaseModel):
    ref: str
    name: str
    domain: str
    tier: str  # strategic | target | standard
    owner: str
    custom: dict = {}


class Contact(BaseModel):
    ref: str
    account_ref: str
    name: str
    title: str
    email: str
    phone: str | None = None
    linkedin_url: str | None = None


class AccountQuery(BaseModel):
    owner: str | None = None
    tier: str | None = None
    last_touched_before: datetime | None = None
    status: str | None = None


class CRMTask(BaseModel):
    ref: str
    account_ref: str
    subject: str
    due_date: date | None = None
    status: str = "open"


class Activity(BaseModel):
    ref: str
    account_ref: str
    contact_ref: str | None = None
    activity_type: str
    subject: str
    body: str | None = None
    occurred_at: datetime


class NormalizedEvent(BaseModel):
    event_type: str
    persona_tier: str | None = None
    points_value: float | None = None
    channel: str | None = None
    account_ref: str
    contact_ref: str | None = None
    occurred_at: datetime
    source: str
    payload: dict = {}


class ActivityWrite(BaseModel):
    account_ref: str
    contact_ref: str | None = None
    activity_type: str
    subject: str
    body: str | None = None


class TaskWrite(BaseModel):
    account_ref: str
    subject: str
    due_date: date | None = None


# ── Email ─────────────────────────────────────────────────────────────

class ThreadSummary(BaseModel):
    ref: str
    subject: str
    last_message_at: datetime
    snippet: str


class ThreadQuery(BaseModel):
    unreplied: bool | None = None
    since: datetime | None = None
    label: str | None = None


class Message(BaseModel):
    id: str
    thread_ref: str
    sender: str
    to: list[str]
    subject: str
    body: str
    sent_at: datetime


class Thread(BaseModel):
    ref: str
    subject: str
    messages: list[Message]


class InboundMessage(BaseModel):
    id: str
    thread_ref: str
    sender: str
    subject: str
    body: str
    received_at: datetime


class AutoReplyInfo(BaseModel):
    is_autoreply: bool
    kind: str | None = None  # ooo | bounce
    return_date: date | None = None


class DraftEmail(BaseModel):
    to: list[str]
    subject: str
    body: str
    thread_ref: str | None = None
    in_reply_to: str | None = None


# ── Calendar ──────────────────────────────────────────────────────────

class Attendee(BaseModel):
    email: str
    response_status: str  # accepted | declined | needsAction


class CalEvent(BaseModel):
    ref: str
    title: str
    start: datetime
    end: datetime
    attendees: list[Attendee] = []
    body: str | None = None
    meeting_link: str | None = None


class Slot(BaseModel):
    start: datetime
    end: datetime
    days_out: int
    pull_in_candidate: bool = False


class SlotCriteria(BaseModel):
    earliest: date
    latest: date
    duration_minutes: int = 30
    preferred_max_days_out: int = 4


class InviteStatus(BaseModel):
    event_ref: str
    attendees: list[Attendee]


class Capacity(BaseModel):
    business_days: int
    pto_dates: list[date] = []
    blocked_hours: float = 0.0


class EventWrite(BaseModel):
    title: str
    start: datetime
    end: datetime
    attendees: list[str]
    body: str | None = None


# ── Enrichment ────────────────────────────────────────────────────────

class CompanyProfile(BaseModel):
    domain: str
    name: str
    size: str | None = None
    funding: str | None = None
    stack: list[str] = []
    eng_headcount_trend: str | None = None


class PersonProfile(BaseModel):
    email: str | None = None
    linkedin_url: str | None = None
    name: str
    title: str
    company: str | None = None


class Signal(BaseModel):
    kind: str
    account_domain: str
    strength: float
    evidence: str
    detected_at: datetime


# ── Call Recording ────────────────────────────────────────────────────

class CallMeta(BaseModel):
    ref: str
    account_ref: str
    contact_ref: str | None = None
    title: str
    occurred_at: datetime
    duration_seconds: int


class Segment(BaseModel):
    speaker: str
    text: str
    start_seconds: float
    end_seconds: float


class Transcript(BaseModel):
    call_ref: str
    segments: list[Segment]
