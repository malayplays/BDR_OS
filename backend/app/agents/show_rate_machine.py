"""Show-Rate Machine — table-driven state machine for meeting show-rate optimization.

Spec: AGENTS.md section 5
States: BOOKED -> INVITE_SENT -> ACCEPTED -> CONFIRMED_24H -> CONFIRMED_AM -> HELD | NO_SHOW
Every transition writes EventLog. All customer-facing jobs require approval.
Transition table loaded from show_rate_machine.yaml.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml

from app.models.enums import EventSource, EventType, FunnelStage, JobStatus
from app.models.meeting_state import (
    IllegalMeetingTransitionError,
    MeetingRecord,
    MeetingRisk,
    MeetingState,
)

logger = logging.getLogger(__name__)

YAML_PATH = Path(__file__).resolve().parent / "show_rate_machine.yaml"


# ---------------------------------------------------------------------------
# Transition table loader
# ---------------------------------------------------------------------------

class TransitionDef:
    """A single transition definition parsed from the YAML table."""

    __slots__ = (
        "from_state", "to_state", "trigger", "job_type",
        "job_description", "customer_facing", "approval_required",
        "max_fires", "min_days_out",
    )

    def __init__(self, d: dict) -> None:
        self.from_state: str = d["from"]
        self.to_state: str = d["to"]
        self.trigger: str = d["trigger"]
        self.job_type: str | None = d.get("job_type")
        self.job_description: str = d.get("job_description", "")
        self.customer_facing: bool = d.get("customer_facing", False)
        self.approval_required: bool = d.get("approval_required", False)
        self.max_fires: int | None = d.get("max_fires")
        self.min_days_out: int | None = d.get("min_days_out")


def load_transition_table() -> list[TransitionDef]:
    with open(YAML_PATH) as f:
        data = yaml.safe_load(f)
    return [TransitionDef(t) for t in data["transitions"]]


def _build_index(table: list[TransitionDef]) -> dict[tuple[str, str], TransitionDef]:
    """Index by (from_state, trigger) for O(1) lookup."""
    idx: dict[tuple[str, str], TransitionDef] = {}
    for t in table:
        idx[(t.from_state, t.trigger)] = t
    return idx


TRANSITION_TABLE = load_transition_table()
TRANSITION_INDEX = _build_index(TRANSITION_TABLE)


def legal_transitions() -> set[tuple[str, str, str]]:
    """Return set of (from_state, trigger, to_state) triples."""
    return {(t.from_state, t.trigger, t.to_state) for t in TRANSITION_TABLE}


def all_states() -> set[str]:
    """All states referenced in the transition table."""
    s: set[str] = set()
    for t in TRANSITION_TABLE:
        s.add(t.from_state)
        s.add(t.to_state)
    return s


# ---------------------------------------------------------------------------
# Clock protocol (for testing with fake clocks)
# ---------------------------------------------------------------------------

class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.utcnow()


# ---------------------------------------------------------------------------
# ShowRateMachine — the core state machine
# ---------------------------------------------------------------------------

_TRIGGER_TO_EVENT_TYPE: dict[str, EventType] = {
    "booking_detected": EventType.MEETING_BOOKED,
    "invite_accepted": EventType.INVITE_ACCEPTED,
    "invite_not_accepted_24h": EventType.INVITE_ACCEPTED,
    "ooo_autoreply": EventType.OOO_AUTOREPLY,
    "timer_t_minus_24h": EventType.MEETING_BOOKED,
    "timer_morning_of": EventType.MEETING_BOOKED,
    "attendance_confirmed": EventType.MEETING_HELD,
    "no_attendance_10min": EventType.MEETING_NO_SHOW,
    "rebooked": EventType.MEETING_RESCHEDULED,
    "pull_in_check": EventType.MEETING_BOOKED,
}


class ShowRateMachine:
    """Table-driven meeting state machine.

    Transition lookup is data-driven from YAML. Illegal transitions raise.
    Every transition writes an EventLog entry and optionally emits a Job.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        table: list[TransitionDef] | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self._table = table or TRANSITION_TABLE
        self._index = _build_index(self._table)
        self.event_log: list[dict[str, Any]] = []
        self.jobs: list[dict[str, Any]] = []
        self.timers: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transition(
        self,
        meeting: MeetingRecord,
        trigger: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a transition. Returns the emitted job dict or None.

        Raises IllegalMeetingTransitionError for invalid (state, trigger) pairs.
        """
        current_state = MeetingState(meeting.state)
        key = (current_state.value, trigger)
        tdef = self._index.get(key)
        if tdef is None:
            raise IllegalMeetingTransitionError(
                current_state,
                MeetingState(current_state),  # just for the error message
            )

        # Enforce max_fires guard (reconfirm)
        if tdef.max_fires is not None:
            if meeting.reconfirm_sent >= tdef.max_fires:
                return None

        # Enforce min_days_out guard (pull_in)
        if tdef.min_days_out is not None:
            if meeting.meeting_start:
                days_out = (meeting.meeting_start - self.clock.now()).days
                if days_out < tdef.min_days_out:
                    return None

        # Apply state transition
        new_state = MeetingState(tdef.to_state)
        meeting.state = new_state.value

        # Track risk
        if trigger == "ooo_autoreply":
            meeting.risk = MeetingRisk.OOO.value
        elif trigger == "invite_not_accepted_24h":
            meeting.risk = MeetingRisk.INVITE_NOT_ACCEPTED.value
            meeting.reconfirm_sent += 1
        elif trigger == "rebooked":
            meeting.risk = MeetingRisk.NONE.value

        # Record pull_in
        if trigger == "pull_in_check":
            meeting.pull_in_offered += 1

        now = self.clock.now()

        # Write EventLog
        event_type = _TRIGGER_TO_EVENT_TYPE.get(trigger, EventType.MEETING_BOOKED)
        payload: dict[str, Any] = {
            "meeting_id": meeting.id,
            "trigger": trigger,
            "from_state": current_state.value,
            "to_state": new_state.value,
            "risk": meeting.risk,
        }
        if trigger == "no_attendance_10min":
            payload["event_ref"] = meeting.event_ref
        event = {
            "event_type": event_type.value,
            "account_ref": meeting.account_ref,
            "contact_ref": meeting.contact_ref,
            "occurred_at": now,
            "source": EventSource.CALENDAR.value,
            "payload": payload,
        }
        self.event_log.append(event)

        # Schedule timers for newly entered states
        self._schedule_timers(meeting, new_state, trigger)

        # Emit job if specified
        if tdef.job_type is None:
            return None

        job = self._build_job(meeting, tdef, now, context)
        self.jobs.append(job)

        return job

    def check_pull_in(self, meeting: MeetingRecord) -> dict[str, Any] | None:
        """Check if pull-in offer should fire at booking time."""
        if meeting.meeting_start is None:
            return None
        return self.transition(meeting, "pull_in_check")

    def check_invite_acceptance(self, meeting: MeetingRecord) -> dict[str, Any] | None:
        """Check invite acceptance 24h after booking."""
        if meeting.state != MeetingState.INVITE_SENT.value:
            return None
        return self.transition(meeting, "invite_not_accepted_24h")

    def check_attendance(self, meeting: MeetingRecord, attended: bool) -> dict[str, Any] | None:
        """Check attendance at start+10min."""
        if meeting.state != MeetingState.CONFIRMED_AM.value:
            return None
        if attended:
            return self.transition(meeting, "attendance_confirmed")
        return self.transition(meeting, "no_attendance_10min")

    def handle_ooo(self, meeting: MeetingRecord) -> dict[str, Any] | None:
        """Handle OOO autoreply from attendee."""
        terminal_states = {MeetingState.HELD.value, MeetingState.NO_SHOW.value, MeetingState.RESCHEDULING.value}
        if meeting.state in terminal_states:
            return None
        return self.transition(meeting, "ooo_autoreply")

    # ------------------------------------------------------------------
    # Timer scheduling
    # ------------------------------------------------------------------

    def _schedule_timers(self, meeting: MeetingRecord, new_state: MeetingState, trigger: str) -> None:
        """Schedule timers based on the new state."""
        if meeting.meeting_start is None:
            return

        meeting_start = meeting.meeting_start
        if isinstance(meeting_start, str):
            meeting_start = datetime.fromisoformat(meeting_start)

        if new_state == MeetingState.INVITE_SENT:
            # Invite-acceptance check at booked+24h
            self.timers.append({
                "meeting_id": meeting.id,
                "timer_type": "invite_acceptance_check",
                "fire_at": self.clock.now() + timedelta(hours=24),
                "trigger": "invite_not_accepted_24h",
            })

        if new_state == MeetingState.ACCEPTED:
            # T-24h confirmation timer
            fire_at = meeting_start - timedelta(hours=24)
            self.timers.append({
                "meeting_id": meeting.id,
                "timer_type": "t_minus_24h",
                "fire_at": fire_at,
                "trigger": "timer_t_minus_24h",
            })

        if new_state == MeetingState.CONFIRMED_24H:
            # Morning-of timer (8am recipient-local)
            meeting_day = meeting_start.date()
            fire_at = datetime(meeting_day.year, meeting_day.month, meeting_day.day, 8, 0, 0)
            self.timers.append({
                "meeting_id": meeting.id,
                "timer_type": "morning_of",
                "fire_at": fire_at,
                "trigger": "timer_morning_of",
            })

        if new_state == MeetingState.CONFIRMED_AM:
            # Attendance check at start+10min
            self.timers.append({
                "meeting_id": meeting.id,
                "timer_type": "attendance_check",
                "fire_at": meeting_start + timedelta(minutes=10),
                "trigger": "no_attendance_10min",
            })

    # ------------------------------------------------------------------
    # Job building
    # ------------------------------------------------------------------

    def _build_job(
        self,
        meeting: MeetingRecord,
        tdef: TransitionDef,
        now: datetime,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build a job dict from a transition definition."""
        signal_ref = meeting.signal_kind or ""
        signal_evidence = meeting.signal_evidence or ""
        brief_angle = meeting.brief_angle or ""

        draft_content = self._generate_draft_content(
            tdef, signal_ref, signal_evidence, brief_angle, context,
        )

        job: dict[str, Any] = {
            "job_type": tdef.job_type,
            "funnel_stage": FunnelStage.HOLD.value,
            "agent": "show_rate_machine",
            "account_ref": meeting.account_ref,
            "contact_ref": meeting.contact_ref,
            "status": JobStatus.PENDING.value,
            "customer_facing": tdef.customer_facing,
            "approval_required": tdef.approval_required,
            "trigger": {
                "type": tdef.trigger,
                "meeting_id": meeting.id,
                "event_ref": meeting.event_ref,
            },
            "input_payload": {
                "meeting_id": meeting.id,
                "meeting_state": meeting.state,
                "event_ref": meeting.event_ref,
                "signal_kind": signal_ref,
                "signal_evidence": signal_evidence,
                "brief_angle": brief_angle,
            },
            "output": {
                "draft": draft_content,
            },
            "created_at": now,
        }

        if tdef.approval_required:
            job["status"] = JobStatus.AWAITING_APPROVAL.value

        return job

    def _generate_draft_content(
        self,
        tdef: TransitionDef,
        signal_kind: str,
        signal_evidence: str,
        brief_angle: str,
        context: dict[str, Any] | None,
    ) -> str:
        """Generate draft content for a job, referencing the originating signal."""
        if tdef.job_type == "send_invite":
            signal_line = (
                f"how {signal_kind} works on a ticket like yours"
                if signal_kind
                else "a live demo on your codebase"
            )
            return (
                f"What you'll see: {signal_line}. "
                f"{signal_evidence} "
                f"Feel free to bring a colleague."
            )

        if tdef.job_type == "confirm_24h":
            signal_line = signal_kind if signal_kind else "your workflow"
            return (
                f"Tomorrow I'll show you the one thing most teams miss about {signal_line} "
                f"— {signal_evidence}. "
                f"Feel free to bring a colleague."
            )

        if tdef.job_type == "confirm_am":
            return (
                f"Quick proof point before our call: {signal_evidence or brief_angle}"
            )

        if tdef.job_type == "reconfirm":
            return (
                "Making sure this still works for you — or here are two other times if not."
            )

        if tdef.job_type == "reschedule":
            return (
                "Noticed you're out of office — no worries! Here are two times that might work "
                "when you're back."
            )

        if tdef.job_type == "pull_in_offer":
            return (
                "A slot opened up sooner — want to grab it? Earlier usually beats calendar-tetris."
            )

        if tdef.job_type == "no_show_recovery":
            return (
                "No-show detected — creating recovery sequence."
            )

        return tdef.job_description
