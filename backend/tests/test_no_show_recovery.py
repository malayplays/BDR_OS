"""Tests for the No-Show Recovery Agent (Session 7).

Merge bar tests:
- test_t10_draft — no-show event → draft within simulated 10min, 2 slots ≤3 days out, no guilt.
- test_thread_check_suppression — "running late" fixture → zero recovery jobs.
- test_sequence_kill_on_reply — reply after touch 1 → touches 2–3 skipped.
- test_single_approval_unit — queue shows one item with all 3 touches; approving schedules all.
- test_rebook_reenters_machine — reschedule → show-rate machine state BOOKED with new event ref.
- Golden tests for AGENTS.md §6.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.agents.no_show_recovery import (
    BANNED_PHRASES,
    approve_recovery_sequence,
    check_thread_for_late_reply,
    contains_banned_phrase,
    handle_reschedule_success,
    kill_sequence_on_reply,
    process_no_show,
)
from app.models.enums import EventType, JobStatus
from app.models.meeting_state import MeetingRecord, MeetingState

# ── Fixtures ──────────────────────────────────────────────────────────

MEETING_START = datetime(2026, 6, 12, 14, 0, 0)

MEETING = {
    "ref": "cal-100",
    "title": "Devin Demo — Acme Corp",
    "start": MEETING_START.isoformat(),
    "end": (MEETING_START + timedelta(minutes=30)).isoformat(),
    "attendees": [
        {"email": "vp@acme.com", "response_status": "accepted"},
        {"email": "malay@example.com", "response_status": "accepted"},
    ],
    "body": "Demo: how Devin handles a real ticket from your backlog.",
    "meeting_link": "https://meet.example.com/demo-100",
    "account_ref": "acct-500",
}

CONTACT = {
    "ref": "contact-100",
    "account_ref": "acct-500",
    "name": "Jane VP",
    "title": "VP Engineering",
    "email": "vp@acme.com",
    "phone": "+1-555-0100",
    "linkedin_url": "https://linkedin.com/in/janevp",
}

SIGNAL = {
    "kind": "hiring_surge",
    "account_domain": "acme.com",
    "strength": 0.85,
    "evidence": "27 open backend roles, +40% eng headcount QoQ",
    "detected_at": "2026-06-10T00:00:00",
}

BRIEF = {
    "angle": "Devin as headcount-multiplier during the hiring crunch",
}

# Slots — two ≤3 days out, one further
SLOTS = [
    {
        "start": (MEETING_START + timedelta(days=1, hours=0)).isoformat(),
        "end": (MEETING_START + timedelta(days=1, hours=0, minutes=30)).isoformat(),
        "days_out": 1,
        "pull_in_candidate": False,
    },
    {
        "start": (MEETING_START + timedelta(days=2, hours=6)).isoformat(),
        "end": (MEETING_START + timedelta(days=2, hours=6, minutes=30)).isoformat(),
        "days_out": 2,
        "pull_in_candidate": False,
    },
    {
        "start": (MEETING_START + timedelta(days=5)).isoformat(),
        "end": (MEETING_START + timedelta(days=5, minutes=30)).isoformat(),
        "days_out": 5,
        "pull_in_candidate": True,
    },
]

CLEAN_THREAD = {
    "ref": "thread-100",
    "subject": "Re: Devin Demo — Acme Corp",
    "messages": [
        {
            "id": "msg-100-1",
            "thread_ref": "thread-100",
            "sender": "malay@example.com",
            "to": ["vp@acme.com"],
            "subject": "Devin Demo — Acme Corp",
            "body": "Looking forward to the demo tomorrow.",
            "sent_at": (MEETING_START - timedelta(days=1)).isoformat(),
        },
    ],
}


def _make_input(meeting=None, contact=None, thread=None, slots=None, signal=None, brief=None):
    return {
        "meeting": meeting or MEETING,
        "contact": contact or CONTACT,
        "thread": thread or CLEAN_THREAD,
        "slots": slots or SLOTS,
        "signal": signal or SIGNAL,
        "brief": brief or BRIEF,
        "prior_confirmations": ["24h_confirm", "am_confirm"],
    }


# ── Running-late thread fixture ───────────────────────────────────────

LATE_THREAD = {
    "ref": "thread-200",
    "subject": "Re: Devin Demo — Acme Corp",
    "messages": [
        {
            "id": "msg-200-1",
            "thread_ref": "thread-200",
            "sender": "malay@example.com",
            "to": ["vp@acme.com"],
            "subject": "Devin Demo — Acme Corp",
            "body": "Looking forward to the demo tomorrow.",
            "sent_at": (MEETING_START - timedelta(days=1)).isoformat(),
        },
        {
            "id": "msg-200-2",
            "thread_ref": "thread-200",
            "sender": "vp@acme.com",
            "to": ["malay@example.com"],
            "subject": "Re: Devin Demo — Acme Corp",
            "body": "Running late, join in 10",
            "sent_at": (MEETING_START + timedelta(minutes=5)).isoformat(),
        },
    ],
}

# ── Helpers ───────────────────────────────────────────────────────────


class JobCollector:
    """Collects jobs created via create_job_fn."""

    def __init__(self):
        self.jobs: list[dict] = []

    def __call__(self, job: dict):
        self.jobs.append(job)


class EventCollector:
    """Collects events logged via event_log_fn."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict):
        self.events.append(event)


# ══════════════════════════════════════════════════════════════════════
# MERGE BAR TESTS
# ══════════════════════════════════════════════════════════════════════


class TestT10Draft:
    """test_t10_draft — no-show event → draft within simulated 10min,
    contains 2 slots ≤3 days out, no guilt phrasing."""

    def test_t10_draft(self):
        jobs = JobCollector()
        result = process_no_show(
            _make_input(),
            create_job_fn=jobs,
            now=MEETING_START + timedelta(minutes=10),
        )

        assert not result["suppressed"]
        assert len(result["jobs_created"]) >= 1

        # Find the reschedule draft job
        reschedule_jobs = [j for j in jobs.jobs if j["job_type"] == "no_show_reschedule"]
        assert len(reschedule_jobs) == 1

        rj = reschedule_jobs[0]
        draft = rj["output"]["draft"]

        # Due at T+10min
        due = datetime.fromisoformat(rj["due_at"])
        assert due == MEETING_START + timedelta(minutes=10)

        # Contains 2 slots
        assert draft["slot_1"] is not None
        assert draft["slot_2"] is not None

        # Both slots ≤3 days out
        s1_start = datetime.fromisoformat(draft["slot_1"]["start"])
        s2_start = datetime.fromisoformat(draft["slot_2"]["start"])
        assert (s1_start - MEETING_START).days <= 3
        assert (s2_start - MEETING_START).days <= 3

        # No guilt phrasing
        body = draft["body"]
        violations = contains_banned_phrase(body)
        assert violations == [], f"Banned phrases found: {violations}"

    def test_t10_draft_slot_labels_present(self):
        """Slot labels appear in draft body."""
        result = process_no_show(
            _make_input(),
            now=MEETING_START + timedelta(minutes=10),
        )
        body = result["output"]["reschedule_draft"]["body"]
        assert "[" in body and "]" in body, "Slot labels should be bracketed in body"

    def test_t10_draft_all_banned_phrases_absent(self):
        """Explicitly check every banned phrase against draft body."""
        result = process_no_show(
            _make_input(),
            now=MEETING_START + timedelta(minutes=10),
        )
        body = result["output"]["reschedule_draft"]["body"].lower()
        for phrase in BANNED_PHRASES:
            assert phrase not in body, f"Banned phrase '{phrase}' found in draft body"

        # Also check the sequence touch bodies
        for touch in result["output"]["sequence"]:
            text = (touch.get("body") or "") + (touch.get("subject") or "")
            for phrase in BANNED_PHRASES:
                assert phrase not in text.lower(), (
                    f"Banned phrase '{phrase}' found in touch {touch['touch_number']}"
                )


class TestThreadCheckSuppression:
    """test_thread_check_suppression — "running late" fixture → zero recovery jobs."""

    def test_thread_check_suppression(self):
        jobs = JobCollector()
        events = EventCollector()
        result = process_no_show(
            _make_input(thread=LATE_THREAD),
            create_job_fn=jobs,
            event_log_fn=events,
            now=MEETING_START + timedelta(minutes=10),
        )

        assert result["suppressed"] is True
        assert len(result["jobs_created"]) == 0
        assert len(jobs.jobs) == 0

    def test_thread_check_emits_meeting_held(self):
        """Suppression emits a meeting_held event."""
        events = EventCollector()
        process_no_show(
            _make_input(thread=LATE_THREAD),
            event_log_fn=events,
            now=MEETING_START + timedelta(minutes=10),
        )

        assert len(events.events) == 1
        ev = events.events[0]
        assert ev["event_type"] == EventType.MEETING_HELD
        assert ev["payload"]["reason"] == "late_reply_detected"

    def test_thread_check_no_late_reply_proceeds(self):
        """Normal thread (no late reply) → recovery proceeds."""
        result = process_no_show(
            _make_input(thread=CLEAN_THREAD),
            now=MEETING_START + timedelta(minutes=10),
        )
        assert result["suppressed"] is False
        assert len(result["jobs_created"]) == 2


class TestSequenceKillOnReply:
    """test_sequence_kill_on_reply — reply after touch 1 → touches 2–3 skipped."""

    def test_sequence_kill_on_reply(self):
        jobs = JobCollector()
        process_no_show(
            _make_input(),
            create_job_fn=jobs,
            now=MEETING_START + timedelta(minutes=10),
        )

        # Get the sequence job and approve it
        seq_jobs = [j for j in jobs.jobs if j["job_type"] == "no_show_recovery_sequence"]
        assert len(seq_jobs) == 1

        approved_jobs = JobCollector()
        scheduled = approve_recovery_sequence(
            seq_jobs[0],
            create_job_fn=approved_jobs,
            no_show_time=MEETING_START,
        )
        assert len(scheduled) == 3

        # Simulate touch 1 sent (written back)
        scheduled[0]["status"] = JobStatus.WRITTEN_BACK

        # Reply arrives → kill remaining
        skipped = kill_sequence_on_reply(scheduled)

        assert len(skipped) == 2
        assert scheduled[0]["status"] == JobStatus.WRITTEN_BACK  # touch 1 unchanged
        assert scheduled[1]["status"] == JobStatus.SKIPPED  # touch 2 skipped
        assert scheduled[2]["status"] == JobStatus.SKIPPED  # touch 3 skipped

    def test_sequence_kill_all_pending(self):
        """If reply arrives before any touch sent, all 3 are skipped."""
        jobs = JobCollector()
        process_no_show(
            _make_input(),
            create_job_fn=jobs,
            now=MEETING_START + timedelta(minutes=10),
        )

        seq_jobs = [j for j in jobs.jobs if j["job_type"] == "no_show_recovery_sequence"]
        scheduled = approve_recovery_sequence(
            seq_jobs[0],
            no_show_time=MEETING_START,
        )

        # Reply before any touch sent
        skipped = kill_sequence_on_reply(scheduled)
        assert len(skipped) == 3
        assert all(j["status"] == JobStatus.SKIPPED for j in scheduled)


class TestSingleApprovalUnit:
    """test_single_approval_unit — queue shows one item containing all 3 touches;
    approving schedules all."""

    def test_single_approval_unit(self):
        jobs = JobCollector()
        process_no_show(
            _make_input(),
            create_job_fn=jobs,
            now=MEETING_START + timedelta(minutes=10),
        )

        # Find the sequence job (ONE approval unit)
        seq_jobs = [j for j in jobs.jobs if j["job_type"] == "no_show_recovery_sequence"]
        assert len(seq_jobs) == 1, "Should be exactly one sequence approval unit"

        seq = seq_jobs[0]
        touches = seq["output"]["touches"]
        assert len(touches) == 3, "Sequence must contain all 3 touches"
        assert seq["output"]["approval_mode"] == "batch"
        assert seq["status"] == JobStatus.AWAITING_APPROVAL

        # Touch details
        assert touches[0]["touch_number"] == 1
        assert touches[0]["delay_days"] == 1
        assert touches[0]["channel"] == "email"

        assert touches[1]["touch_number"] == 2
        assert touches[1]["delay_days"] == 3
        assert touches[1]["channel"] == "call"

        assert touches[2]["touch_number"] == 3
        assert touches[2]["delay_days"] == 7
        assert touches[2]["channel"] == "email"

        # Approve → 3 scheduled jobs
        scheduled_collector = JobCollector()
        scheduled = approve_recovery_sequence(
            seq,
            create_job_fn=scheduled_collector,
            no_show_time=MEETING_START,
        )

        assert len(scheduled) == 3
        assert len(scheduled_collector.jobs) == 3

        # Each has correct due_at
        for touch, sched_job in zip(touches, scheduled):
            expected_due = MEETING_START + timedelta(days=touch["delay_days"])
            actual_due = datetime.fromisoformat(sched_job["due_at"])
            assert actual_due == expected_due

        # Sequence job marked approved
        assert seq["status"] == JobStatus.APPROVED


class TestRebookReentersMachine:
    """test_rebook_reenters_machine — reschedule → show-rate machine state BOOKED
    with new event ref."""

    def test_rebook_reenters_machine(self):
        events = EventCollector()
        meeting_record = {
            "id": "mr-001",
            "event_ref": "cal-100",
            "account_ref": "acct-500",
            "contact_ref": "contact-100",
            "state": MeetingState.NO_SHOW,  # "NO_SHOW" (uppercase, Session 6)
        }

        updated = handle_reschedule_success(
            meeting_record,
            "cal-200",
            event_log_fn=events,
            now=MEETING_START + timedelta(hours=2),
        )

        # State is BOOKED
        assert updated["state"] == MeetingState.BOOKED

        # Event ref is new
        assert updated["event_ref"] == "cal-200"

        # meeting_rescheduled event emitted
        assert len(events.events) == 1
        ev = events.events[0]
        assert ev["event_type"] == EventType.MEETING_RESCHEDULED
        assert ev["payload"]["old_event_ref"] == "cal-100"
        assert ev["payload"]["new_event_ref"] == "cal-200"

    def test_rebook_with_orm_model(self, db_session):
        """Works with SQLAlchemy MeetingRecord too."""
        events = EventCollector()

        rec = MeetingRecord(
            event_ref="cal-100",
            account_ref="acct-500",
            contact_ref="contact-100",
            state=MeetingState.NO_SHOW,
            risk="none",
        )
        db_session.add(rec)
        db_session.flush()

        updated = handle_reschedule_success(
            rec,
            "cal-300",
            event_log_fn=events,
            now=MEETING_START + timedelta(hours=2),
        )

        assert updated.state == MeetingState.BOOKED
        assert updated.event_ref == "cal-300"
        assert len(events.events) == 1


# ══════════════════════════════════════════════════════════════════════
# GOLDEN TESTS — AGENTS.md §6
# ══════════════════════════════════════════════════════════════════════


class TestGoldenT10Reschedule:
    """Golden (T+10min) — "No worries at all — calendars happen. Two quick
    options to regrab 15 min: [Thu 2:00] [Fri 10:30]. Same agenda: the
    [signal] walkthrough."
    """

    def test_golden_t10_reschedule(self):
        result = process_no_show(
            _make_input(),
            now=MEETING_START + timedelta(minutes=10),
        )

        draft = result["output"]["reschedule_draft"]
        body = draft["body"]

        # Core phrases from golden
        assert "No worries" in body
        assert "calendars happen" in body
        assert "Two quick options" in body or "regrab 15 min" in body
        assert "walkthrough" in body

        # Signal topic referenced
        assert "hiring crunch" in body or "headcount-multiplier" in body or "demo" in body.lower()

        # Two slot labels bracketed
        assert body.count("[") >= 2
        assert body.count("]") >= 2

        # Zero guilt
        for phrase in BANNED_PHRASES:
            assert phrase not in body.lower()


class TestGoldenThreadSuppressionNegative:
    """Golden (negative) — contact replied "running late, join in 10"?
    → recovery suppressed, meeting_held path; agent checks thread before drafting.
    """

    def test_golden_thread_suppression_negative(self):
        events = EventCollector()
        result = process_no_show(
            _make_input(thread=LATE_THREAD),
            event_log_fn=events,
            now=MEETING_START + timedelta(minutes=10),
        )

        # Recovery suppressed
        assert result["suppressed"] is True
        assert result["output"] is None
        assert len(result["jobs_created"]) == 0

        # meeting_held path
        assert len(events.events) == 1
        assert events.events[0]["event_type"] == EventType.MEETING_HELD
        assert events.events[0]["account_ref"] == "acct-500"
        assert events.events[0]["contact_ref"] == "contact-100"


# ══════════════════════════════════════════════════════════════════════
# UNIT TESTS — thread check / banned phrases
# ══════════════════════════════════════════════════════════════════════


class TestCheckThreadForLateReply:
    def test_running_late_detected(self):
        messages = [
            {
                "sender": "vp@acme.com",
                "body": "Running late, join in 10",
                "sent_at": (MEETING_START + timedelta(minutes=3)).isoformat(),
            }
        ]
        assert check_thread_for_late_reply(messages, MEETING_START, "vp@acme.com") is True

    def test_almost_there(self):
        messages = [
            {
                "sender": "vp@acme.com",
                "body": "Almost there!",
                "sent_at": (MEETING_START + timedelta(minutes=2)).isoformat(),
            }
        ]
        assert check_thread_for_late_reply(messages, MEETING_START, "vp@acme.com") is True

    def test_outside_window_not_detected(self):
        messages = [
            {
                "sender": "vp@acme.com",
                "body": "Running late",
                "sent_at": (MEETING_START + timedelta(hours=2)).isoformat(),
            }
        ]
        assert check_thread_for_late_reply(messages, MEETING_START, "vp@acme.com") is False

    def test_different_sender_ignored(self):
        messages = [
            {
                "sender": "other@acme.com",
                "body": "Running late",
                "sent_at": (MEETING_START + timedelta(minutes=3)).isoformat(),
            }
        ]
        assert check_thread_for_late_reply(messages, MEETING_START, "vp@acme.com") is False

    def test_no_late_phrase_not_detected(self):
        messages = [
            {
                "sender": "vp@acme.com",
                "body": "Can we move this to next week?",
                "sent_at": (MEETING_START + timedelta(minutes=5)).isoformat(),
            }
        ]
        assert check_thread_for_late_reply(messages, MEETING_START, "vp@acme.com") is False


class TestContainsBannedPhrase:
    def test_you_missed(self):
        assert contains_banned_phrase("Sorry you missed our call") == ["you missed"]

    def test_no_show(self):
        assert contains_banned_phrase("This is a no-show follow up") == ["no-show"]

    def test_waited(self):
        assert contains_banned_phrase("I waited 15 minutes") == ["waited"]

    def test_clean(self):
        assert contains_banned_phrase("No worries at all — calendars happen.") == []

    def test_multiple(self):
        result = contains_banned_phrase("You missed the call. I waited.")
        assert "you missed" in result
        assert "waited" in result
