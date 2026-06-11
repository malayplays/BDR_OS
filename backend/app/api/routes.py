from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.enums import JobStatus
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
