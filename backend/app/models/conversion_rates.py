from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class ConversionRates(Base):
    __tablename__ = "conversion_rates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    persona_tier: Mapped[str | None] = mapped_column(String(30), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    n_sample: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_rate: Mapped[float] = mapped_column(Float, nullable=False)
    k_strength: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    blended_rate: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False, default="low")
    baseline_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
