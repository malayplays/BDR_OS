"""Plain dataclasses for the engine layer — zero I/O, zero SQLAlchemy/FastAPI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

# ── Enums (engine-local copies so engine/ never imports models/) ──────


class Channel(StrEnum):
    EMAIL = "email"
    CALL = "call"
    LINKEDIN = "linkedin"


class RateMetric(StrEnum):
    REPLY_RATE = "reply_rate"
    POSITIVE_REPLY_RATE = "positive_reply_rate"
    BOOK_RATE = "book_rate"
    SHOW_RATE = "show_rate"
    QUALIFY_RATE = "qualify_rate"
    AD_ACCEPT_RATE = "ad_accept_rate"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EventType(StrEnum):
    TOUCH_SENT = "touch_sent"
    REPLY_RECEIVED = "reply_received"
    POSITIVE_REPLY = "positive_reply"
    MEETING_BOOKED = "meeting_booked"
    MEETING_HELD = "meeting_held"
    MEETING_NO_SHOW = "meeting_no_show"
    MEETING_RESCHEDULED = "meeting_rescheduled"
    MEETING_CANCELLED = "meeting_cancelled"
    AD_ACCEPTED = "ad_accepted"
    AD_REJECTED = "ad_rejected"
    S1_REACHED = "s1_reached"
    S2_REACHED = "s2_reached"
    CREDIT_CLAWED_BACK = "credit_clawed_back"
    DORMANCY_REQUALIFIED = "dormancy_requalified"
    INVITE_ACCEPTED = "invite_accepted"
    INVITE_DECLINED = "invite_declined"
    OOO_AUTOREPLY = "ooo_autoreply"
    BOUNCE = "bounce"
    UNSUBSCRIBE = "unsubscribe"


class PersonaTier(StrEnum):
    GLOBAL_C_SUITE = "global_c_suite"
    VP_LEVEL = "vp_level"
    DIRECTOR = "director"
    MANAGER = "manager"
    IC = "ic"


class ReplanReason(StrEnum):
    WEEKLY_CASCADE = "weekly_cascade"
    PACE_GAP = "pace_gap"
    RATE_DRIFT = "rate_drift"
    GOAL_EDITED = "goal_edited"
    CAPACITY_CHANGE = "capacity_change"


class FunnelStage(StrEnum):
    CREATE = "create"
    CONVERT = "convert"
    HOLD = "hold"


# ── Data containers ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Event:
    event_type: str
    occurred_at: datetime
    channel: str | None = None
    persona_tier: str | None = None
    points_value: float | None = None
    account_ref: str = ""
    contact_ref: str | None = None
    source: str = "mock"
    payload: dict = field(default_factory=dict)
    reverses_event_id: str | None = None


@dataclass(frozen=True)
class Goal:
    id: str
    unit: str
    target_value: float
    period_type: str
    period_start: date
    period_end: date
    parent_goal_id: str | None = None
    edited_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class RateRow:
    """One computed rate observation."""
    metric: str
    channel: str | None
    window_days: int
    n_sample: int
    actual_rate: float | None
    benchmark_rate: float
    k_strength: int
    blended_rate: float
    confidence: str
    baseline_90d: float | None
    computed_at: datetime
    persona_tier: str | None = None


@dataclass(frozen=True)
class Capacity:
    business_days: int
    pto_dates: tuple[date, ...] = ()
    blocked_hours: float = 0.0


@dataclass(frozen=True)
class DailyAllocation:
    day: date
    email_touches: float
    calls: float
    linkedin_touches: float
    call_blocks: tuple[dict, ...] = ()
    confirmations_due: int = 0


@dataclass(frozen=True)
class Plan:
    """Immutable cascade output — no update path; regeneration supersedes."""
    id: str
    goal_id: str
    week_start: date
    weekly_bookings_required: float
    weekly_held_target: float
    daily_allocations: tuple[DailyAllocation, ...]
    rates_snapshot: dict
    capacity: Capacity
    generated_at: datetime
    superseded_at: datetime | None = None
    replan_reason: str = "weekly_cascade"


@dataclass(frozen=True)
class FunnelCounts:
    touches_email: int = 0
    touches_call: int = 0
    touches_linkedin: int = 0
    replies: int = 0
    positive_replies: int = 0
    booked: int = 0
    held: int = 0
    no_shows: int = 0
    ad_accepted: int = 0
    s1: int = 0
    s2: int = 0


@dataclass(frozen=True)
class PointsBucket:
    credited: float = 0.0
    pending: float = 0.0
    projected: float = 0.0


@dataclass(frozen=True)
class FunnelState:
    goal_id: str
    as_of: datetime
    counts: FunnelCounts
    points: PointsBucket
    persona_mix: dict = field(default_factory=dict)
    pct_goal: float = 0.0
    pct_period_elapsed: float = 0.0
    pace_gap: float = 0.0
    gap_by_stage: dict = field(default_factory=dict)
    at_risk: bool = False


@dataclass(frozen=True)
class ReplanTrigger:
    reason: ReplanReason
    detail: str
    fired_at: datetime


@dataclass(frozen=True)
class BottleneckResult:
    stage: FunnelStage
    priority: int
    reason: str


@dataclass(frozen=True)
class CatchupLever:
    name: str
    description: str
    estimated_delta_held: float
    attention_cost_hours: float
    stage: str


@dataclass(frozen=True)
class CatchupPlan:
    levers: tuple[CatchupLever, ...]
    daily_inflation_pct: float
    at_risk: bool
    shortfall: float = 0.0
    shortfall_detail: str = ""


# ── Seed benchmarks ──────────────────────────────────────────────────

SEED_BENCHMARKS: dict[tuple[str, str | None], float] = {
    (RateMetric.REPLY_RATE, Channel.EMAIL): 0.04,
    (RateMetric.REPLY_RATE, Channel.CALL): 0.08,
    (RateMetric.REPLY_RATE, Channel.LINKEDIN): 0.08,
    (RateMetric.POSITIVE_REPLY_RATE, None): 0.35,
    (RateMetric.BOOK_RATE, None): 0.55,
    (RateMetric.SHOW_RATE, None): 0.70,
    (RateMetric.QUALIFY_RATE, None): 0.60,
    (RateMetric.AD_ACCEPT_RATE, None): 0.90,
}


# Default persona point values from COMP_MODEL.md §2
PERSONA_POINTS: dict[str, float] = {
    PersonaTier.GLOBAL_C_SUITE: 8.0,
    PersonaTier.VP_LEVEL: 5.0,
    PersonaTier.DIRECTOR: 3.0,
    PersonaTier.MANAGER: 1.0,
    PersonaTier.IC: 0.5,
}

# Default dials-per-hour for call-block sizing
DIALS_PER_HOUR: float = 12.0
