from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    weekly_bookings_required: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    weekly_held_target: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    daily_allocation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rates_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capacity: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replan_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
