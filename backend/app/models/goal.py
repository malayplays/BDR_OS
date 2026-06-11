from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    parent_goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"), nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
