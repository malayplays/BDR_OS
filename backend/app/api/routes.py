from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.conversion_rates import ConversionRates
from app.models.enums import EventType, JobStatus, RateMetric
from app.models.event_log import EventLog
from app.models.goal import Goal
from app.models.job import InvalidTransitionError, Job
from app.models.meeting_state import MeetingRecord, MeetingState
from app.schemas import JobApproval, JobRead

router = APIRouter(prefix="/api")


def _advance_to_approvable(job: Job) -> None:
    """Walk the state machine to awaiting_approval so approve/reject can proceed."""
    status = JobStatus(job.status)
    if status == JobStatus.PENDING:
        job.transition_to(JobStatus.IN_PROGRESS)
        status = JobStatus.IN_PROGRESS
    if status == JobStatus.IN_PROGRESS:
        job.transition_to(JobStatus.AWAITING_APPROVAL)


# ── Existing job CRUD ─────────────────────────────────────────────────


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(db: Session = Depends(get_db)):
    return db.query(Job).order_by(Job.created_at.desc()).all()


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/approve", response_model=JobRead)
def approve_job(job_id: str, body: JobApproval | None = None, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        _advance_to_approvable(job)
        job.transition_to(JobStatus.APPROVED)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    now = datetime.utcnow()
    approval_data = {
        "decided_by": body.decided_by if body else "user",
        "decided_at": now.isoformat(),
        "edit_diff": body.edit_diff if body else None,
    }
    job.approval = approval_data
    job.updated_at = now
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/reject", response_model=JobRead)
def reject_job(job_id: str, body: JobApproval | None = None, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        _advance_to_approvable(job)
        job.transition_to(JobStatus.REJECTED)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    now = datetime.utcnow()
    approval_data = {
        "decided_by": body.decided_by if body else "user",
        "decided_at": now.isoformat(),
        "rejection_reason": body.rejection_reason if body else None,
    }
    job.approval = approval_data
    job.updated_at = now
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/skip", response_model=JobRead)
def skip_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        job.transition_to(JobStatus.SKIPPED)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/snooze", response_model=JobRead)
def snooze_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.PENDING, JobStatus.IN_PROGRESS):
        raise HTTPException(status_code=409, detail=f"Cannot snooze job in {job.status} state")
    now = datetime.utcnow()
    job.due_at = now + timedelta(hours=2)
    job.updated_at = now
    db.commit()
    db.refresh(job)
    return job


# ── /api/today — Today tab ────────────────────────────────────────────

_ACTIVE_STATUSES = {JobStatus.PENDING, JobStatus.IN_PROGRESS, JobStatus.AWAITING_APPROVAL}


def _count_confirmations_due(db: Session) -> int:
    return (
        db.query(MeetingRecord)
        .filter(MeetingRecord.state.in_([MeetingState.ACCEPTED, MeetingState.CONFIRMED_24H]))
        .count()
    )


def _detect_bottleneck(db: Session) -> str:
    held = (
        db.query(sa_func.count())
        .select_from(EventLog)
        .filter(EventLog.event_type == EventType.MEETING_HELD)
        .scalar()
    ) or 0
    booked = (
        db.query(sa_func.count())
        .select_from(EventLog)
        .filter(EventLog.event_type == EventType.MEETING_BOOKED)
        .scalar()
    ) or 0

    if booked > 0 and held / booked < 0.70:
        return "Show rate is the bottleneck — prioritise hold-stage confirmations."
    positive = (
        db.query(sa_func.count())
        .select_from(EventLog)
        .filter(EventLog.event_type == EventType.POSITIVE_REPLY)
        .scalar()
    ) or 0
    if positive == 0:
        return "No positive replies yet — ramp outreach volume."
    return "On track — no critical bottleneck detected."


@router.get("/today")
def get_today(db: Session = Depends(get_db)) -> dict[str, Any]:
    jobs = (
        db.query(Job)
        .filter(Job.status.in_([s.value for s in _ACTIVE_STATUSES]))
        .order_by(Job.priority_score.desc())
        .all()
    )

    confirmations_due = _count_confirmations_due(db)
    bottleneck = _detect_bottleneck(db)

    # Compute simple points summary from event log
    credited = (
        db.query(sa_func.coalesce(sa_func.sum(EventLog.points_value), 0.0))
        .filter(EventLog.event_type == EventType.AD_ACCEPTED)
        .scalar()
    ) or 0.0
    # Pending = points from booked/held meetings not yet AD-accepted
    pending_pts = (
        db.query(sa_func.coalesce(sa_func.sum(EventLog.points_value), 0.0))
        .filter(EventLog.event_type == EventType.MEETING_BOOKED)
        .scalar()
    ) or 0.0
    pending = max(0.0, pending_pts - credited)

    # Goal target
    goal = db.query(Goal).order_by(Goal.edited_at.desc()).first()
    target = goal.target_value if goal else 35.0

    plan_data: dict[str, Any] = {
        "daily_allocation": {"email": 12, "call": 25, "linkedin": 4},
        "next_call_block": "10:00",
        "confirmations_due": confirmations_due,
        "bottleneck": bottleneck,
    }

    return {
        "plan": plan_data,
        "jobs": [_job_dict(j) for j in jobs],
        "pace": {"credited": round(credited, 2), "pending": round(pending, 2), "target": target},
    }


# ── /api/review-queue — Queue tab ─────────────────────────────────────

_REVIEW_STATUSES = {JobStatus.PENDING, JobStatus.AWAITING_APPROVAL}
_CUSTOMER_FACING_TYPES = {
    "book_response",
    "outreach_draft",
    "send_invite",
    "reconfirm",
    "reminder_24h",
    "reminder_am",
    "no_show_recovery",
    "reschedule",
    "pull_in_offer",
}


@router.get("/review-queue")
def get_review_queue(db: Session = Depends(get_db)) -> dict[str, Any]:
    items = (
        db.query(Job)
        .filter(
            Job.status.in_([s.value for s in _REVIEW_STATUSES]),
            Job.job_type.in_(_CUSTOMER_FACING_TYPES),
        )
        .order_by(Job.due_at.asc().nullslast(), Job.priority_score.desc())
        .all()
    )

    groups_map: dict[str, dict[str, Any]] = {}
    for item in items:
        jt = item.job_type
        if jt not in groups_map:
            groups_map[jt] = {"name": jt, "count": 0, "has_sla": False}
        groups_map[jt]["count"] += 1
        if item.due_at:
            groups_map[jt]["has_sla"] = True

    groups = sorted(groups_map.values(), key=lambda g: (0 if g["name"] == "book_response" else 1, g["name"]))

    return {
        "items": [_job_dict(j) for j in items],
        "groups": groups,
    }


# ── /api/pace — Pace tab ──────────────────────────────────────────────

PERSONA_POINTS: dict[str, float] = {
    "global_c_suite": 8.0,
    "vp_level": 5.0,
    "director": 3.0,
    "manager": 1.0,
    "ic": 0.5,
}


def _count_events(db: Session, event_type: str) -> int:
    return (
        db.query(sa_func.count()).select_from(EventLog).filter(EventLog.event_type == event_type).scalar()
    ) or 0


@router.get("/pace")
def get_pace(db: Session = Depends(get_db)) -> dict[str, Any]:
    # Points
    credited = float(
        db.query(sa_func.coalesce(sa_func.sum(EventLog.points_value), 0.0))
        .filter(EventLog.event_type == EventType.AD_ACCEPTED)
        .scalar()
        or 0.0
    )
    pending_booked = float(
        db.query(sa_func.coalesce(sa_func.sum(EventLog.points_value), 0.0))
        .filter(EventLog.event_type == EventType.MEETING_BOOKED)
        .scalar()
        or 0.0
    )
    pending = max(0.0, pending_booked - credited)

    goal = db.query(Goal).order_by(Goal.edited_at.desc()).first()
    target = goal.target_value if goal else 35.0

    today = date.today()
    if goal:
        total_days = max((goal.period_end - goal.period_start).days, 1)
        elapsed_days = max((today - goal.period_start).days, 0)
        pct_elapsed = min(elapsed_days / total_days, 1.0)
    else:
        pct_elapsed = today.day / 30.0

    expected_pts = target * pct_elapsed
    pace_gap = ((credited + pending) - expected_pts) / target * 100 if target else 0.0

    # Funnel counts
    touches = _count_events(db, EventType.TOUCH_SENT)
    replies = _count_events(db, EventType.REPLY_RECEIVED)
    positive = _count_events(db, EventType.POSITIVE_REPLY)
    booked = _count_events(db, EventType.MEETING_BOOKED)
    held = _count_events(db, EventType.MEETING_HELD)
    ad_accepted = _count_events(db, EventType.AD_ACCEPTED)

    # Expected funnel (simple model from rates or defaults)
    exp_touches = 40 * max(today.day, 1)
    funnel = {
        "touches": {"actual": touches, "expected": exp_touches},
        "replies": {"actual": replies, "expected": max(int(exp_touches * 0.04), 1)},
        "positive": {"actual": positive, "expected": max(int(exp_touches * 0.03), 1)},
        "booked": {"actual": booked, "expected": max(int(exp_touches * 0.015), 1)},
        "held": {"actual": held, "expected": max(int(exp_touches * 0.01), 1)},
        "ad_accepted": {"actual": ad_accepted, "expected": max(int(exp_touches * 0.008), 1)},
    }

    # Rates
    rates_rows = db.query(ConversionRates).order_by(ConversionRates.computed_at.desc()).all()
    if rates_rows:
        rates = [
            {
                "metric": r.metric,
                "blended_rate": r.blended_rate * 100,
                "n_sample": r.n_sample,
                "confidence": r.confidence,
                "drift": round((r.blended_rate - (r.baseline_90d or r.blended_rate)) * 100, 1),
            }
            for r in rates_rows
        ]
    else:
        rates = [
            {"metric": m, "blended_rate": d * 100, "n_sample": 0, "confidence": "low", "drift": 0.0}
            for m, d in [
                (RateMetric.REPLY_RATE, 0.04),
                (RateMetric.POSITIVE_REPLY_RATE, 0.03),
                (RateMetric.BOOK_RATE, 0.50),
                (RateMetric.SHOW_RATE, 0.70),
                (RateMetric.QUALIFY_RATE, 0.60),
                (RateMetric.AD_ACCEPT_RATE, 0.80),
            ]
        ]

    # Earnings projection (COMP_MODEL §2 placeholders)
    base = 2500
    pts_rate = 100  # $/point simplified
    accelerator = round(credited * pts_rate * 0.28, 2) if credited > 0 else 0
    spiff = 1000 if credited >= 10 else 0
    projected = base + accelerator + spiff
    annualized = round(projected * 12, 2)

    # Promotion scorecard placeholder
    promotion = {
        "streak": [credited >= target * 1.3 / 12] * min(today.month, 6),
        "sourced_s2": f"{ad_accepted} of 3",
        "months_above_40": "0 consecutive",
        "status": "Tracking — data accumulating",
    }

    return {
        "goal": {
            "credited": round(credited, 2),
            "pending": round(pending, 2),
            "target": target,
            "pct_period_elapsed": round(pct_elapsed, 3),
            "pace_gap": round(pace_gap, 1),
        },
        "funnel": funnel,
        "rates": rates,
        "earnings": {
            "projected": projected,
            "base": base,
            "accelerator": accelerator,
            "spiff": spiff,
            "annualized": annualized,
            "target_annual": 135000,
        },
        "promotion": promotion,
    }


# ── Helpers ────────────────────────────────────────────────────────────


def _job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "funnel_stage": job.funnel_stage,
        "agent": job.agent,
        "trigger": job.trigger,
        "account_ref": job.account_ref,
        "contact_ref": job.contact_ref,
        "status": job.status,
        "expected_value": job.expected_value,
        "priority_score": job.priority_score,
        "input_payload": job.input_payload,
        "output": job.output,
        "policy_flags": job.policy_flags,
        "approval": job.approval,
        "write_back_ref": job.write_back_ref,
        "due_at": job.due_at.isoformat() if job.due_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
