"""Meeting state model for the Show-Rate Machine.

States: BOOKED -> INVITE_SENT -> ACCEPTED -> CONFIRMED_24H -> CONFIRMED_AM -> HELD | NO_SHOW
Risk sub-transitions: any state can have risk events (OOO, invite-not-accepted).
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class MeetingState(StrEnum):
    BOOKED = "BOOKED"
    INVITE_SENT = "INVITE_SENT"
    ACCEPTED = "ACCEPTED"
    CONFIRMED_24H = "CONFIRMED_24H"
    CONFIRMED_AM = "CONFIRMED_AM"
    HELD = "HELD"
    NO_SHOW = "NO_SHOW"
    RESCHEDULING = "RESCHEDULING"


class MeetingRisk(StrEnum):
    NONE = "none"
    INVITE_NOT_ACCEPTED = "invite_not_accepted"
    OOO = "ooo"


class IllegalMeetingTransitionError(Exception):
    def __init__(self, current: MeetingState, target: MeetingState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Illegal meeting transition: {current} -> {target}")


class MeetingRecord(Base):
    """Persistent record of a meeting being shepherded through the show-rate machine."""

    __tablename__ = "meeting_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    event_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default=MeetingState.BOOKED)
    risk: Mapped[str] = mapped_column(String(30), nullable=False, default=MeetingRisk.NONE)
    signal_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    signal_evidence: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    brief_angle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    meeting_start: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    meeting_end: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    recipient_tz: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    reconfirm_sent: Mapped[int] = mapped_column(default=0)
    pull_in_offered: Mapped[int] = mapped_column(default=0)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
