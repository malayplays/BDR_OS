from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid
from app.models.enums import JobStatus

ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.IN_PROGRESS, JobStatus.SKIPPED, JobStatus.EXPIRED},
    JobStatus.IN_PROGRESS: {JobStatus.AWAITING_APPROVAL, JobStatus.FAILED},
    JobStatus.AWAITING_APPROVAL: {JobStatus.APPROVED, JobStatus.REJECTED, JobStatus.EDITED_APPROVED, JobStatus.EXPIRED},
    JobStatus.APPROVED: {JobStatus.WRITTEN_BACK, JobStatus.FAILED},
    JobStatus.EDITED_APPROVED: {JobStatus.WRITTEN_BACK, JobStatus.FAILED},
    JobStatus.REJECTED: set(),
    JobStatus.WRITTEN_BACK: set(),
    JobStatus.EXPIRED: set(),
    JobStatus.FAILED: set(),
    JobStatus.SKIPPED: set(),
}


class InvalidTransitionError(Exception):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current} → {target}")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    funnel_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    agent: Mapped[str] = mapped_column(String(50), nullable=False)
    trigger: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    account_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=JobStatus.PENDING)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    write_back_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def transition_to(self, target: JobStatus) -> None:
        current = JobStatus(self.status)
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTransitionError(current, target)
        self.status = target
