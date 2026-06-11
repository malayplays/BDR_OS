from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dispatcher.job_factory import estimated_minutes
from app.dispatcher.morning_plan import build_today_payload
from app.engine.types import Event
from app.models.enums import JobStatus
from app.models.event_log import EventLog
from app.models.job import InvalidTransitionError, Job
from app.schemas import JobApproval, JobRead

router = APIRouter(prefix="/api")


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


@router.post("/jobs/{job_id}/skip", response_model=JobRead)
def skip_job(job_id: str, db: Session = Depends(get_db)):
    """Skip a job — moves it to SKIPPED terminal state."""
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


class SnoozeBody(BaseModel):
    days: int = 1


SNOOZE_DECAY_FACTOR: float = 0.9


@router.post("/jobs/{job_id}/snooze", response_model=JobRead)
def snooze_job(job_id: str, body: SnoozeBody | None = None, db: Session = Depends(get_db)):
    """Snooze a job — re-enters tomorrow with decayed boost.

    The job stays PENDING but due_at shifts to tomorrow.
    priority_score is decayed by SNOOZE_DECAY_FACTOR so snoozed jobs
    don't accumulate and silently outrank fresh work.
    The snooze count is tracked in input_payload to prevent silent drops.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.PENDING, JobStatus.IN_PROGRESS):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot snooze job in {job.status} state",
        )

    now = datetime.utcnow()
    days = body.days if body else 1

    job.due_at = now + timedelta(days=days)
    job.priority_score = job.priority_score * SNOOZE_DECAY_FACTOR

    payload = dict(job.input_payload or {})
    payload["snooze_count"] = payload.get("snooze_count", 0) + 1
    payload["last_snoozed_at"] = now.isoformat()
    job.input_payload = payload

    job.updated_at = now
    db.commit()
    db.refresh(job)
    return job


@router.get("/today")
def get_today(db: Session = Depends(get_db)):
    """Morning plan — ranked Today list + narrative."""
    now = datetime.utcnow()

    pending_jobs = (
        db.query(Job)
        .filter(Job.status.in_([JobStatus.PENDING, JobStatus.IN_PROGRESS]))
        .all()
    )

    job_dicts = []
    for j in pending_jobs:
        job_dicts.append({
            "id": j.id,
            "job_type": j.job_type,
            "funnel_stage": j.funnel_stage,
            "agent": j.agent,
            "trigger": j.trigger,
            "account_ref": j.account_ref,
            "contact_ref": j.contact_ref,
            "status": j.status,
            "expected_value": j.expected_value,
            "priority_score": j.priority_score,
            "estimated_minutes": estimated_minutes(j.job_type),
            "input_payload": j.input_payload,
            "due_at": j.due_at,
            "created_at": j.created_at,
        })

    cutoff = now - timedelta(days=30)
    event_rows = db.query(EventLog).filter(EventLog.occurred_at >= cutoff).all()
    events = [
        Event(
            event_type=e.event_type,
            occurred_at=e.occurred_at,
            channel=e.channel,
            persona_tier=e.persona_tier,
            account_ref=e.account_ref or "",
            contact_ref=e.contact_ref,
        )
        for e in event_rows
    ]

    payload = build_today_payload(
        jobs=job_dicts,
        plan=None,
        rates=[],
        events=events,
        now=now,
    )
    return payload


@router.post("/jobs/{job_id}/reject", response_model=JobRead)
def reject_job(job_id: str, body: JobApproval | None = None, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
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
