from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    persona_tier: Mapped[str | None] = mapped_column(String(30), nullable=True)
    points_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reverses_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_event_type_occurred", "event_type", "occurred_at"),
        Index("ix_account_ref", "account_ref"),
        Index("ix_job_id", "job_id"),
    )
