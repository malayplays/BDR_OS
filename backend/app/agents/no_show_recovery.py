"""No-Show Recovery Agent — Session 7.

Spec: AGENTS.md §6
- Trigger: meeting_no_show event
- T+10min: polite zero-guilt reschedule w/ two one-click times ≤3 days out
- 3-touch sequence: +1d value nudge, +3d channel-switch call task, +7d graceful close
- Pre-draft thread check: suppress if contact replied "running late" near meeting time
- Sequence killed instantly on any inbound reply
- 3-touch sequence approves as ONE Review Queue unit; sends are scheduled drafts
- Reschedule success → meeting_rescheduled → re-enters show-rate machine at BOOKED
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentBase
from app.models.enums import EventSource, EventType, JobStatus
from app.models.meeting_state import MeetingRecord, MeetingState

# ── Banned phrases (zero-guilt language) ──────────────────────────────

BANNED_PHRASES = ["you missed", "no-show", "waited"]


def contains_banned_phrase(text: str) -> list[str]:
    """Return any banned phrases found in text."""
    lower = text.lower()
    return [p for p in BANNED_PHRASES if p in lower]


# ── Thread check for late reply ───────────────────────────────────────

LATE_PATTERNS = [
    r"running late",
    r"be there in \d+",
    r"joining in \d+",
    r"join in \d+",
    r"on my way",
    r"just a few minutes",
    r"be right there",
    r"almost there",
]


def check_thread_for_late_reply(
    thread_messages: list[dict],
    meeting_start: datetime,
    contact_email: str,
    window_minutes: int = 30,
) -> bool:
    """Check if contact replied near meeting time with a 'running late' message.

    Returns True if recovery should be SUPPRESSED.
    """
    window_start = meeting_start - timedelta(minutes=window_minutes)
    window_end = meeting_start + timedelta(minutes=window_minutes)

    for msg in thread_messages:
        sender = msg.get("sender", "")
        if sender != contact_email:
            continue

        sent_at_raw = msg.get("sent_at") or msg.get("received_at")
        if not sent_at_raw:
            continue

        sent_at = datetime.fromisoformat(sent_at_raw) if isinstance(sent_at_raw, str) else sent_at_raw

        if window_start <= sent_at <= window_end:
            body = (msg.get("body") or "").lower()
            for pattern in LATE_PATTERNS:
                if re.search(pattern, body):
                    return True

    return False


# ── Output schemas ────────────────────────────────────────────────────


class SlotOption(BaseModel):
    start: datetime
    end: datetime
    label: str


class RescheduleDraft(BaseModel):
    subject: str
    body: str
    slot_1: SlotOption
    slot_2: SlotOption


class SequenceTouch(BaseModel):
    touch_number: int  # 1, 2, 3
    delay_days: int  # +1, +3, +7
    channel: str  # "email", "call", "email"
    subject: str | None = None
    body: str | None = None
    task_description: str | None = None


class NoShowRecoveryLLMOutput(BaseModel):
    """Schema validated against LLM output."""

    reschedule_subject: str
    reschedule_body: str
    touch_1_subject: str
    touch_1_body: str
    touch_2_task: str
    touch_3_subject: str
    touch_3_body: str
    confidence: float
    needs_human_because: str | None = None


class NoShowRecoveryOutput(BaseModel):
    """Full structured output from the no-show recovery process."""

    reschedule_draft: RescheduleDraft
    sequence: list[SequenceTouch]
    suppressed: bool = False
    suppression_reason: str | None = None


# ── Agent (LLM draft generation) ─────────────────────────────────────


class NoShowRecoveryAgent(AgentBase):
    """Generates no-show recovery drafts via LLM."""

    agent_name = "no_show_recovery"

    def _system_prompt(self) -> str:
        return (
            "You are a No-Show Recovery agent for a BDR automation system.\n\n"
            "A prospect no-showed a meeting. Generate:\n"
            "1. A polite T+10min reschedule email (zero guilt language)\n"
            "2. A 3-touch follow-up sequence:\n"
            "   - Touch 1 (+1 day): Value nudge with a new proof point\n"
            "   - Touch 2 (+3 days): Call task description (channel switch)\n"
            "   - Touch 3 (+7 days): Graceful close with open door\n\n"
            "NEVER use: 'you missed', 'no-show', 'waited'.\n"
            "Output ONLY valid JSON matching the schema. No markdown."
        )

    def _build_user_message(self, job_input: dict) -> str:
        return json.dumps(job_input, indent=2, default=str)

    def _output_schema(self) -> type[NoShowRecoveryLLMOutput]:
        return NoShowRecoveryLLMOutput


# ── Helpers ───────────────────────────────────────────────────────────


def _format_slot_label(dt: datetime) -> str:
    """Format datetime as 'Thu 2:00'."""
    return dt.strftime("%a %-I:%M")


def _pick_slots(slots: list[dict], max_days_out: int = 3) -> list[dict]:
    """Pick up to 2 slots ≤ max_days_out."""
    eligible = sorted(
        [s for s in slots if s.get("days_out", 999) <= max_days_out],
        key=lambda s: s.get("days_out", 999),
    )
    if len(eligible) >= 2:
        return eligible[:2]
    # Pad from remaining slots
    all_sorted = sorted(slots, key=lambda s: s.get("days_out", 999))
    for s in all_sorted:
        if s not in eligible:
            eligible.append(s)
        if len(eligible) >= 2:
            break
    return eligible[:2]


def _parse_dt(raw: str | datetime | None) -> datetime | None:
    if raw is None:
        return None
    return datetime.fromisoformat(raw) if isinstance(raw, str) else raw


def _build_slot_option(slot_dict: dict) -> SlotOption:
    start = _parse_dt(slot_dict.get("start")) or datetime.utcnow()
    end = _parse_dt(slot_dict.get("end")) or start + timedelta(minutes=30)
    return SlotOption(start=start, end=end, label=_format_slot_label(start))


# ── Main pipeline ─────────────────────────────────────────────────────


def process_no_show(
    input_payload: dict,
    *,
    create_job_fn: Any = None,
    event_log_fn: Any = None,
    now: datetime | None = None,
) -> dict:
    """Full no-show recovery pipeline.

    1. Pre-draft thread check (suppress if "running late")
    2. Build T+10 reschedule draft
    3. Build 3-touch sequence
    4. Create jobs: one reschedule + one batch-approval sequence

    Returns {suppressed, suppression_reason, jobs_created, output}.
    """
    if now is None:
        now = datetime.utcnow()

    meeting = input_payload.get("meeting", {})
    contact = input_payload.get("contact", {})
    thread = input_payload.get("thread", {})
    slots = input_payload.get("slots", [])

    meeting_start = _parse_dt(meeting.get("start")) or now
    contact_email = contact.get("email", "")
    thread_messages = thread.get("messages", [])
    account_ref = contact.get("account_ref", meeting.get("account_ref", ""))
    contact_ref = contact.get("ref", "")

    # ── Step 1: Pre-draft thread check ────────────────────────────
    if check_thread_for_late_reply(thread_messages, meeting_start, contact_email):
        if event_log_fn:
            event_log_fn({
                "event_type": EventType.MEETING_HELD,
                "account_ref": account_ref,
                "contact_ref": contact_ref,
                "occurred_at": now.isoformat(),
                "source": EventSource.EMAIL,
                "payload": {
                    "reason": "late_reply_detected",
                    "meeting_ref": meeting.get("ref", ""),
                },
            })
        return {
            "suppressed": True,
            "suppression_reason": "Contact replied near meeting time indicating they were running late",
            "jobs_created": [],
            "output": None,
        }

    # ── Step 2: Pick slots ≤3 days out ────────────────────────────
    chosen = _pick_slots(slots, max_days_out=3)
    slot_opts = [_build_slot_option(s) for s in chosen]

    # ── Step 3: Build drafts ──────────────────────────────────────
    signal_evidence = input_payload.get("signal", {}).get("evidence", "")
    brief_angle = input_payload.get("brief", {}).get("angle", "")
    topic = brief_angle or signal_evidence or "the demo"

    s1_label = slot_opts[0].label if slot_opts else "TBD"
    s2_label = slot_opts[1].label if len(slot_opts) > 1 else "TBD"

    reschedule_draft = RescheduleDraft(
        subject=f"Re: {meeting.get('title', 'Our meeting')}",
        body=(
            f"No worries at all \u2014 calendars happen. "
            f"Two quick options to regrab 15 min: "
            f"[{s1_label}] [{s2_label}]. "
            f"Same agenda: the {topic} walkthrough."
        ),
        slot_1=slot_opts[0] if slot_opts else SlotOption(start=now, end=now, label="TBD"),
        slot_2=slot_opts[1] if len(slot_opts) > 1 else SlotOption(start=now, end=now, label="TBD"),
    )

    sequence = [
        SequenceTouch(
            touch_number=1,
            delay_days=1,
            channel="email",
            subject=f"Quick thought on {topic}",
            body=(
                f"Wanted to share this \u2014 teams scaling engineering "
                f"are seeing 30% faster onboarding with AI pair programming. "
                f"Still happy to walk through how it works on a real ticket. "
                f"[{s1_label}] or [{s2_label}] if either opens up."
            ),
        ),
        SequenceTouch(
            touch_number=2,
            delay_days=3,
            channel="call",
            task_description=(
                f"Warm call to {contact.get('name', 'contact')}: "
                f"reference the {topic} walkthrough, offer to reschedule. 60 seconds max."
            ),
        ),
        SequenceTouch(
            touch_number=3,
            delay_days=7,
            channel="email",
            subject="Closing the loop",
            body=(
                f"Looks like timing isn't right \u2014 totally understand. "
                f"If {topic} comes back on the radar, "
                f"happy to pick this up. No need to reply \u2014 just keeping the door open."
            ),
        ),
    ]

    # ── Step 4: Create jobs ───────────────────────────────────────
    jobs_created: list[dict] = []

    # Reschedule draft — due at T+10min
    reschedule_job = {
        "job_type": "no_show_reschedule",
        "funnel_stage": "hold",
        "agent": "no_show_recovery",
        "account_ref": account_ref,
        "contact_ref": contact_ref,
        "trigger": {"type": "meeting_no_show", "meeting_ref": meeting.get("ref", "")},
        "input_payload": input_payload,
        "output": {"draft": reschedule_draft.model_dump(mode="json"), "type": "reschedule"},
        "due_at": (meeting_start + timedelta(minutes=10)).isoformat(),
        "status": JobStatus.AWAITING_APPROVAL,
    }
    if create_job_fn:
        create_job_fn(reschedule_job)
    jobs_created.append(reschedule_job)

    # Recovery sequence — ONE approval unit, all 3 touches
    sequence_job = {
        "job_type": "no_show_recovery_sequence",
        "funnel_stage": "hold",
        "agent": "no_show_recovery",
        "account_ref": account_ref,
        "contact_ref": contact_ref,
        "trigger": {"type": "meeting_no_show", "meeting_ref": meeting.get("ref", "")},
        "input_payload": input_payload,
        "output": {
            "touches": [t.model_dump(mode="json") for t in sequence],
            "type": "recovery_sequence",
            "approval_mode": "batch",
        },
        "due_at": (meeting_start + timedelta(days=1)).isoformat(),
        "status": JobStatus.AWAITING_APPROVAL,
    }
    if create_job_fn:
        create_job_fn(sequence_job)
    jobs_created.append(sequence_job)

    return {
        "suppressed": False,
        "suppression_reason": None,
        "jobs_created": jobs_created,
        "output": NoShowRecoveryOutput(
            reschedule_draft=reschedule_draft,
            sequence=sequence,
        ).model_dump(mode="json"),
    }


# ── Approval: batch approve the 3-touch sequence ─────────────────────


def approve_recovery_sequence(
    sequence_job: dict,
    *,
    create_job_fn: Any = None,
    no_show_time: datetime | None = None,
) -> list[dict]:
    """Approve the batch and schedule individual touch jobs.

    Each touch gets its own job with due_at = no_show_time + delay_days.
    During DRAFT_ONLY these are scheduled drafts (ready for manual send).
    """
    if no_show_time is None:
        no_show_time = datetime.utcnow()

    touches = sequence_job.get("output", {}).get("touches", [])
    account_ref = sequence_job.get("account_ref", "")
    contact_ref = sequence_job.get("contact_ref", "")
    trigger = sequence_job.get("trigger")

    scheduled: list[dict] = []
    for touch in touches:
        due = no_show_time + timedelta(days=touch["delay_days"])
        job = {
            "job_type": f"no_show_touch_{touch['touch_number']}",
            "funnel_stage": "hold",
            "agent": "no_show_recovery",
            "account_ref": account_ref,
            "contact_ref": contact_ref,
            "trigger": trigger,
            "output": touch,
            "due_at": due.isoformat(),
            "status": JobStatus.APPROVED,
            "sequence_ref": sequence_job.get("id"),
        }
        if create_job_fn:
            create_job_fn(job)
        scheduled.append(job)

    # Mark the parent sequence job as approved
    sequence_job["status"] = JobStatus.APPROVED

    return scheduled


# ── Sequence kill on reply ────────────────────────────────────────────


def kill_sequence_on_reply(touch_jobs: list[dict]) -> list[dict]:
    """Kill remaining pending/approved touches on inbound reply.

    Returns list of jobs that were skipped.
    """
    skippable = {
        JobStatus.PENDING,
        JobStatus.APPROVED,
        JobStatus.AWAITING_APPROVAL,
        JobStatus.IN_PROGRESS,
    }
    skipped: list[dict] = []
    for job in touch_jobs:
        if job.get("status") in skippable:
            job["status"] = JobStatus.SKIPPED
            skipped.append(job)
    return skipped


# ── Rebook handler ────────────────────────────────────────────────────


def handle_reschedule_success(
    meeting_record: MeetingRecord | dict,
    new_event_ref: str,
    *,
    event_log_fn: Any = None,
    now: datetime | None = None,
) -> MeetingRecord | dict:
    """Handle successful reschedule.

    Emits meeting_rescheduled event and re-enters show-rate machine at BOOKED.
    """
    if now is None:
        now = datetime.utcnow()

    # Extract refs — works for both ORM model and dict
    if isinstance(meeting_record, dict):
        acct = meeting_record.get("account_ref", "")
        cref = meeting_record.get("contact_ref", "")
        old_ref = meeting_record.get("event_ref", "")
    else:
        acct = meeting_record.account_ref or ""
        cref = meeting_record.contact_ref or ""
        old_ref = meeting_record.event_ref

    if event_log_fn:
        event_log_fn({
            "event_type": EventType.MEETING_RESCHEDULED,
            "account_ref": acct,
            "contact_ref": cref,
            "occurred_at": now.isoformat(),
            "source": EventSource.CALENDAR,
            "payload": {"old_event_ref": old_ref, "new_event_ref": new_event_ref},
        })

    # Re-enter show-rate machine at BOOKED
    if isinstance(meeting_record, dict):
        meeting_record["state"] = MeetingState.BOOKED
        meeting_record["event_ref"] = new_event_ref
    else:
        meeting_record.state = MeetingState.BOOKED
        meeting_record.event_ref = new_event_ref

    return meeting_record
