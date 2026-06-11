from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class FunnelState(Base):
    __tablename__ = "funnel_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    points: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    persona_mix: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pct_goal: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pct_period_elapsed: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pace_gap: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gap_by_stage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
