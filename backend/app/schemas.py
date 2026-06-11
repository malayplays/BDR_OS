from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.enums import (
    Channel,
    Confidence,
    EventSource,
    EventType,
    FunnelStage,
    GoalUnit,
    JobStatus,
    PeriodType,
    PersonaTier,
    RateMetric,
    ReplanReason,
    VerdictResult,
)

# ── Goal ──────────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    unit: GoalUnit = GoalUnit.POINTS
    target_value: float
    period_type: PeriodType
    period_start: date
    period_end: date
    parent_goal_id: str | None = None


class GoalRead(GoalCreate):
    id: str
    edited_at: datetime

    model_config = {"from_attributes": True}


# ── EventLog ──────────────────────────────────────────────────────────

class EventLogCreate(BaseModel):
    event_type: EventType
    persona_tier: PersonaTier | None = None
    points_value: float | None = None
    channel: Channel | None = None
    account_ref: str
    contact_ref: str | None = None
    job_id: str | None = None
    occurred_at: datetime
    source: EventSource
    payload: dict | None = None
    reverses_event_id: str | None = None


class EventLogRead(EventLogCreate):
    id: str
    ingested_at: datetime

    model_config = {"from_attributes": True}


# ── ConversionRates ───────────────────────────────────────────────────

class ConversionRatesCreate(BaseModel):
    metric: RateMetric
    persona_tier: PersonaTier | None = None
    channel: Channel | None = None
    window_days: int = 30
    n_sample: int = 0
    actual_rate: float | None = None
    benchmark_rate: float
    k_strength: int = 30
    blended_rate: float
    confidence: Confidence = Confidence.LOW
    baseline_90d: float | None = None


class ConversionRatesRead(ConversionRatesCreate):
    id: str
    computed_at: datetime

    model_config = {"from_attributes": True}


# ── FunnelState ───────────────────────────────────────────────────────

class FunnelStateCreate(BaseModel):
    goal_id: str
    counts: dict | None = None
    points: dict | None = None
    persona_mix: dict | None = None
    pct_goal: float = 0.0
    pct_period_elapsed: float = 0.0
    pace_gap: float = 0.0
    gap_by_stage: dict | None = None
    at_risk: bool = False


class FunnelStateRead(FunnelStateCreate):
    id: str
    as_of: datetime

    model_config = {"from_attributes": True}


# ── Plan ──────────────────────────────────────────────────────────────

class PlanCreate(BaseModel):
    goal_id: str
    week_start: date
    weekly_bookings_required: float = 0.0
    weekly_held_target: float = 0.0
    daily_allocation: dict | None = None
    rates_snapshot: dict | None = None
    capacity: dict | None = None
    replan_reason: ReplanReason | None = None


class PlanRead(PlanCreate):
    id: str
    generated_at: datetime
    superseded_at: datetime | None = None

    model_config = {"from_attributes": True}


# ── Job ───────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    job_type: str
    funnel_stage: FunnelStage
    agent: str
    trigger: dict | None = None
    account_ref: str | None = None
    contact_ref: str | None = None
    expected_value: float = 0.0
    priority_score: float = 0.0
    input_payload: dict | None = None
    due_at: datetime | None = None


class JobRead(BaseModel):
    id: str
    job_type: str
    funnel_stage: str
    agent: str
    trigger: dict | None = None
    account_ref: str | None = None
    contact_ref: str | None = None
    status: JobStatus
    expected_value: float
    priority_score: float
    input_payload: dict | None = None
    output: dict | None = None
    policy_flags: dict | None = None
    approval: dict | None = None
    write_back_ref: str | None = None
    due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobApproval(BaseModel):
    decided_by: str = "user"
    edit_diff: dict | None = None
    rejection_reason: str | None = None


# ── Verdict ───────────────────────────────────────────────────────────

class Verdict(BaseModel):
    result: VerdictResult
    reason: str | None = None
    policy_flag: str | None = None
    issued_at: datetime = Field(default_factory=datetime.utcnow)
