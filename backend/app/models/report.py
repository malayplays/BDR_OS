from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    report_type: Mapped[str] = mapped_column(String(30), nullable=False)  # personal_recap | manager_update
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)  # friday | month_end
    goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    write_back_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
