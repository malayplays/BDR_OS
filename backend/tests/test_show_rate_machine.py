"""Tests for the Show-Rate Machine — Session 6.

Eight required tests per spec:
1. test_transition_table — every legal transition succeeds; every illegal pair raises; EventLog audit trail
2. test_happy_path_e2e — full happy path with fake clock
3. test_invite_not_accepted_24h — reconfirm fires once, not repeatedly
4. test_ooo_reroute — OOO autoreply -> reschedule job, state preserved for rebooking
5. test_pull_in_offer — >4 days out -> pull_in; <=4 days -> none
6. test_no_show_handoff — no-show -> meeting_no_show event + recovery job
7. test_confirmations_carry_content — 24h confirm contains signal string + "colleague"
8. test_all_jobs_gated — zero customer-facing job reaches written_back without approval
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.agents.show_rate_machine import (
    ShowRateMachine,
    legal_transitions,
    load_transition_table,
)
from app.models.enums import EventType, JobStatus
from app.models.meeting_state import (
    IllegalMeetingTransitionError,
    MeetingRecord,
    MeetingRisk,
    MeetingState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClock:
    """Controllable clock for deterministic tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 6, 10, 9, 0, 0)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)

    def set(self, dt: datetime) -> None:
        self._now = dt


def _make_meeting(
    *,
    state: MeetingState = MeetingState.BOOKED,
    meeting_start: datetime | None = None,
    signal_kind: str = "hiring_surge",
    signal_evidence: str = "27 open backend roles, +40% eng headcount QoQ",
    brief_angle: str = "Frame Devin as headcount-multiplier during hiring crunch",
    account_ref: str = "acct-100",
    contact_ref: str = "contact-100",
    event_ref: str = "cal-100",
    **kwargs,
) -> MeetingRecord:
    defaults = {
        "id": "meeting-001",
        "event_ref": event_ref,
        "account_ref": account_ref,
        "contact_ref": contact_ref,
        "state": state.value,
        "risk": MeetingRisk.NONE.value,
        "signal_kind": signal_kind,
        "signal_evidence": signal_evidence,
        "brief_angle": brief_angle,
        "meeting_start": meeting_start or datetime(2026, 6, 15, 14, 0, 0),
        "meeting_end": (meeting_start or datetime(2026, 6, 15, 14, 0, 0)) + timedelta(minutes=30),
        "recipient_tz": "America/New_York",
        "reconfirm_sent": 0,
        "pull_in_offered": 0,
        "created_at": datetime(2026, 6, 10, 9, 0, 0),
        "updated_at": datetime(2026, 6, 10, 9, 0, 0),
    }
    defaults.update(kwargs)
    return MeetingRecord(**defaults)


# ---------------------------------------------------------------------------
# 1. test_transition_table
# ---------------------------------------------------------------------------

class TestTransitionTable:
    """Every legal transition from the YAML succeeds; every illegal pair raises;
    full EventLog audit trail."""

    def test_all_legal_transitions_succeed(self):
        table = load_transition_table()
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)

        for tdef in table:
            meeting = _make_meeting(
                state=MeetingState(tdef.from_state),
                meeting_start=datetime(2026, 6, 20, 14, 0, 0),
            )
            # Ensure pull-in has enough days out
            if tdef.min_days_out:
                meeting.meeting_start = clock.now() + timedelta(days=tdef.min_days_out + 2)

            machine.event_log.clear()
            machine.jobs.clear()

            result = machine.transition(meeting, tdef.trigger)

            # State updated
            assert meeting.state == tdef.to_state, (
                f"Expected {tdef.to_state} after ({tdef.from_state}, {tdef.trigger}), "
                f"got {meeting.state}"
            )

            # EventLog written for every transition
            assert len(machine.event_log) >= 1, (
                f"No EventLog for ({tdef.from_state}, {tdef.trigger})"
            )
            ev = machine.event_log[0]
            assert ev["account_ref"] == meeting.account_ref
            assert ev["payload"]["trigger"] == tdef.trigger
            assert ev["payload"]["from_state"] == tdef.from_state
            assert ev["payload"]["to_state"] == tdef.to_state

            # Job emitted when expected
            if tdef.job_type:
                assert result is not None, (
                    f"Expected job {tdef.job_type} for ({tdef.from_state}, {tdef.trigger})"
                )
                assert result["job_type"] == tdef.job_type

    def test_illegal_transitions_raise(self):
        """Every (state, trigger) pair NOT in the YAML raises."""
        legal = legal_transitions()
        legal_pairs = {(t[0], t[1]) for t in legal}

        all_triggers = {t[1] for t in legal}
        all_from_states = {s.value for s in MeetingState}

        machine = ShowRateMachine(clock=FakeClock())

        illegal_count = 0
        for state in all_from_states:
            for trigger in all_triggers:
                if (state, trigger) not in legal_pairs:
                    meeting = _make_meeting(state=MeetingState(state))
                    with pytest.raises(IllegalMeetingTransitionError):
                        machine.transition(meeting, trigger)
                    illegal_count += 1

        assert illegal_count > 0, "Should have tested at least one illegal pair"

    def test_event_log_audit_trail(self):
        """Full audit trail: each transition produces a complete event log entry."""
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)
        meeting = _make_meeting()

        machine.transition(meeting, "booking_detected")
        assert len(machine.event_log) == 1
        ev = machine.event_log[0]
        assert ev["event_type"] == EventType.MEETING_BOOKED.value
        assert ev["source"] == "calendar"
        assert ev["payload"]["from_state"] == "BOOKED"
        assert ev["payload"]["to_state"] == "INVITE_SENT"


# ---------------------------------------------------------------------------
# 2. test_happy_path_e2e
# ---------------------------------------------------------------------------

class TestHappyPathE2E:
    """Fixture booking -> invite job -> (approve) -> accepted -> 24h confirm job
    at T-24h -> AM job -> HELD; assert exact job sequence and timing."""

    def test_full_happy_path(self):
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        # Meeting scheduled for June 15 at 2pm
        meeting = _make_meeting(
            meeting_start=datetime(2026, 6, 15, 14, 0, 0),
        )

        # Step 1: BOOKED -> INVITE_SENT (booking detected)
        job1 = machine.transition(meeting, "booking_detected")
        assert meeting.state == MeetingState.INVITE_SENT.value
        assert job1 is not None
        assert job1["job_type"] == "send_invite"
        assert job1["approval_required"] is True

        # Step 2: INVITE_SENT -> ACCEPTED (invite accepted)
        clock.advance(hours=6)
        result2 = machine.transition(meeting, "invite_accepted")
        assert meeting.state == MeetingState.ACCEPTED.value
        assert result2 is None  # no job emitted

        # Verify T-24h timer was scheduled
        t24h_timers = [t for t in machine.timers if t["timer_type"] == "t_minus_24h"]
        assert len(t24h_timers) == 1
        expected_fire = datetime(2026, 6, 14, 14, 0, 0)
        assert t24h_timers[0]["fire_at"] == expected_fire

        # Step 3: ACCEPTED -> CONFIRMED_24H at T-24h
        clock.set(datetime(2026, 6, 14, 14, 0, 0))
        job3 = machine.transition(meeting, "timer_t_minus_24h")
        assert meeting.state == MeetingState.CONFIRMED_24H.value
        assert job3 is not None
        assert job3["job_type"] == "confirm_24h"
        assert job3["approval_required"] is True

        # Verify morning-of timer scheduled
        am_timers = [t for t in machine.timers if t["timer_type"] == "morning_of"]
        assert len(am_timers) == 1
        assert am_timers[0]["fire_at"] == datetime(2026, 6, 15, 8, 0, 0)

        # Step 4: CONFIRMED_24H -> CONFIRMED_AM (morning-of)
        clock.set(datetime(2026, 6, 15, 8, 0, 0))
        job4 = machine.transition(meeting, "timer_morning_of")
        assert meeting.state == MeetingState.CONFIRMED_AM.value
        assert job4 is not None
        assert job4["job_type"] == "confirm_am"
        assert job4["approval_required"] is True

        # Verify attendance check timer at start+10min
        att_timers = [t for t in machine.timers if t["timer_type"] == "attendance_check"]
        assert len(att_timers) == 1
        assert att_timers[0]["fire_at"] == datetime(2026, 6, 15, 14, 10, 0)

        # Step 5: CONFIRMED_AM -> HELD (attendance confirmed)
        clock.set(datetime(2026, 6, 15, 14, 10, 0))
        result5 = machine.transition(meeting, "attendance_confirmed")
        assert meeting.state == MeetingState.HELD.value
        assert result5 is None  # no job for HELD

        # Verify exact job sequence
        job_types = [j["job_type"] for j in machine.jobs]
        assert job_types == ["send_invite", "confirm_24h", "confirm_am"]

        # Verify full event log trail
        assert len(machine.event_log) == 5  # 5 transitions


# ---------------------------------------------------------------------------
# 3. test_invite_not_accepted_24h
# ---------------------------------------------------------------------------

class TestInviteNotAccepted24h:
    """Reconfirm job fires once, not repeatedly."""

    def test_reconfirm_fires_once(self):
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(state=MeetingState.INVITE_SENT)

        # First reconfirm — should fire
        job1 = machine.transition(meeting, "invite_not_accepted_24h")
        assert job1 is not None
        assert job1["job_type"] == "reconfirm"
        assert meeting.reconfirm_sent == 1
        assert meeting.state == MeetingState.INVITE_SENT.value  # stays in same state

        # Second reconfirm — should be suppressed (max_fires: 1)
        job2 = machine.transition(meeting, "invite_not_accepted_24h")
        assert job2 is None
        assert meeting.reconfirm_sent == 1  # not incremented again

    def test_check_invite_acceptance_helper(self):
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(state=MeetingState.INVITE_SENT)
        job = machine.check_invite_acceptance(meeting)
        assert job is not None
        assert job["job_type"] == "reconfirm"

        # Won't fire again
        job2 = machine.check_invite_acceptance(meeting)
        assert job2 is None

    def test_check_invite_acceptance_wrong_state(self):
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)
        meeting = _make_meeting(state=MeetingState.ACCEPTED)
        result = machine.check_invite_acceptance(meeting)
        assert result is None


# ---------------------------------------------------------------------------
# 4. test_ooo_reroute
# ---------------------------------------------------------------------------

class TestOOOReroute:
    """OOO autoreply mid-state -> reschedule job, state preserved for rebooking."""

    @pytest.mark.parametrize("from_state", [
        MeetingState.BOOKED,
        MeetingState.INVITE_SENT,
        MeetingState.ACCEPTED,
        MeetingState.CONFIRMED_24H,
        MeetingState.CONFIRMED_AM,
    ])
    def test_ooo_from_any_active_state(self, from_state: MeetingState):
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)
        meeting = _make_meeting(state=from_state)

        job = machine.handle_ooo(meeting)
        assert meeting.state == MeetingState.RESCHEDULING.value
        assert meeting.risk == MeetingRisk.OOO.value
        assert job is not None
        assert job["job_type"] == "reschedule"
        assert job["approval_required"] is True

    def test_ooo_then_rebook(self):
        """After OOO reschedule, rebooked returns to BOOKED for new cycle."""
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)
        meeting = _make_meeting(state=MeetingState.ACCEPTED)

        # OOO
        machine.handle_ooo(meeting)
        assert meeting.state == MeetingState.RESCHEDULING.value

        # Rebooked
        machine.transition(meeting, "rebooked")
        assert meeting.state == MeetingState.BOOKED.value
        assert meeting.risk == MeetingRisk.NONE.value

    def test_ooo_ignored_in_terminal_states(self):
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)

        for terminal in [MeetingState.HELD, MeetingState.NO_SHOW, MeetingState.RESCHEDULING]:
            meeting = _make_meeting(state=terminal)
            result = machine.handle_ooo(meeting)
            assert result is None


# ---------------------------------------------------------------------------
# 5. test_pull_in_offer
# ---------------------------------------------------------------------------

class TestPullInOffer:
    """Booking 7 days out -> pull_in job; booking 2 days out -> none."""

    def test_pull_in_7_days_out(self):
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        # 7 days out
        meeting = _make_meeting(
            meeting_start=datetime(2026, 6, 17, 14, 0, 0),
        )
        job = machine.check_pull_in(meeting)
        assert job is not None
        assert job["job_type"] == "pull_in_offer"
        assert job["approval_required"] is True

    def test_no_pull_in_2_days_out(self):
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        # 2 days out — below min_days_out (5)
        meeting = _make_meeting(
            meeting_start=datetime(2026, 6, 12, 14, 0, 0),
        )
        job = machine.check_pull_in(meeting)
        assert job is None

    def test_no_pull_in_4_days_out(self):
        """Exactly 4 days out — still below threshold."""
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            meeting_start=datetime(2026, 6, 14, 14, 0, 0),
        )
        job = machine.check_pull_in(meeting)
        assert job is None

    def test_pull_in_5_days_out(self):
        """Exactly 5 days — at threshold, should trigger."""
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            meeting_start=datetime(2026, 6, 15, 14, 0, 0),
        )
        job = machine.check_pull_in(meeting)
        assert job is not None
        assert job["job_type"] == "pull_in_offer"


# ---------------------------------------------------------------------------
# 6. test_no_show_handoff
# ---------------------------------------------------------------------------

class TestNoShowHandoff:
    """Scripted no-show fixture -> meeting_no_show event + recovery job at start+10min."""

    def test_no_show_creates_event_and_recovery_job(self):
        meeting_start = datetime(2026, 6, 15, 14, 0, 0)
        clock = FakeClock(start=meeting_start + timedelta(minutes=10))
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            state=MeetingState.CONFIRMED_AM,
            meeting_start=meeting_start,
        )

        job = machine.check_attendance(meeting, attended=False)

        # State -> NO_SHOW
        assert meeting.state == MeetingState.NO_SHOW.value

        # Recovery job created
        assert job is not None
        assert job["job_type"] == "no_show_recovery"

        # meeting_no_show event in log
        no_show_events = [
            e for e in machine.event_log
            if e["event_type"] == EventType.MEETING_NO_SHOW.value
        ]
        assert len(no_show_events) == 1
        assert no_show_events[0]["payload"]["meeting_id"] == meeting.id
        assert no_show_events[0]["payload"]["event_ref"] == meeting.event_ref

    def test_no_show_at_start_plus_10min(self):
        """Attendance check fires at start+10min as per timer scheduling."""
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        meeting_start = datetime(2026, 6, 15, 14, 0, 0)
        meeting = _make_meeting(
            state=MeetingState.CONFIRMED_24H,
            meeting_start=meeting_start,
        )

        # Transition to CONFIRMED_AM to schedule attendance timer
        machine.transition(meeting, "timer_morning_of")
        assert meeting.state == MeetingState.CONFIRMED_AM.value

        # Check timer was set at start+10min
        att_timers = [t for t in machine.timers if t["timer_type"] == "attendance_check"]
        assert len(att_timers) == 1
        assert att_timers[0]["fire_at"] == meeting_start + timedelta(minutes=10)

        # Simulate timer fire
        clock.set(meeting_start + timedelta(minutes=10))
        job = machine.check_attendance(meeting, attended=False)
        assert job is not None
        assert job["job_type"] == "no_show_recovery"
        assert meeting.state == MeetingState.NO_SHOW.value

    def test_attendance_confirmed_goes_held(self):
        """If attendance confirmed, state -> HELD, no recovery job."""
        clock = FakeClock(start=datetime(2026, 6, 15, 14, 10, 0))
        machine = ShowRateMachine(clock=clock)
        meeting = _make_meeting(state=MeetingState.CONFIRMED_AM)

        result = machine.check_attendance(meeting, attended=True)
        assert meeting.state == MeetingState.HELD.value
        assert result is None  # no job for HELD


# ---------------------------------------------------------------------------
# 7. test_confirmations_carry_content
# ---------------------------------------------------------------------------

class TestConfirmationsCarryContent:
    """24h confirm draft contains signal-derived string and 'colleague' phrase."""

    def test_24h_confirm_contains_signal(self):
        clock = FakeClock(start=datetime(2026, 6, 14, 14, 0, 0))
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            state=MeetingState.ACCEPTED,
            signal_kind="hiring_surge",
            signal_evidence="27 open backend roles, +40% eng headcount QoQ",
        )

        job = machine.transition(meeting, "timer_t_minus_24h")
        assert job is not None

        draft = job["output"]["draft"]
        # Must contain signal-derived string
        assert "hiring_surge" in draft
        assert "27 open backend roles" in draft
        # Must contain "colleague"
        assert "colleague" in draft.lower()

    def test_invite_also_carries_signal(self):
        """Send invite also references signal and colleague."""
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            signal_kind="hiring_surge",
            signal_evidence="27 open backend roles, +40% eng headcount QoQ",
        )

        job = machine.transition(meeting, "booking_detected")
        assert job is not None

        draft = job["output"]["draft"]
        assert "hiring_surge" in draft
        assert "colleague" in draft.lower()

    def test_generic_confirmation_is_a_failure(self):
        """Confirm with no signal content would be generic — we verify it's NOT."""
        clock = FakeClock(start=datetime(2026, 6, 14, 14, 0, 0))
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            state=MeetingState.ACCEPTED,
            signal_kind="hiring_surge",
            signal_evidence="27 open backend roles, +40% eng headcount QoQ",
        )

        job = machine.transition(meeting, "timer_t_minus_24h")
        draft = job["output"]["draft"]

        # Should NOT be a generic "looking forward to it" style message
        assert "looking forward" not in draft.lower()
        # Must have actual signal content
        assert len(draft) > 50


# ---------------------------------------------------------------------------
# 8. test_all_jobs_gated
# ---------------------------------------------------------------------------

class TestAllJobsGated:
    """Zero customer-facing job from this machine reaches written_back
    without approval during DRAFT_ONLY."""

    def test_customer_facing_jobs_require_approval(self):
        """All customer-facing jobs must have approval_required=True."""
        table = load_transition_table()
        customer_facing_defs = [t for t in table if t.customer_facing and t.job_type]
        assert len(customer_facing_defs) > 0, "Should have customer-facing job definitions"

        for tdef in customer_facing_defs:
            assert tdef.approval_required, (
                f"Customer-facing job {tdef.job_type} "
                f"({tdef.from_state}->{tdef.to_state}) must require approval"
            )

    def test_no_written_back_without_approval(self):
        """Run the full happy path and confirm no job reaches written_back."""
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            meeting_start=datetime(2026, 6, 15, 14, 0, 0),
        )

        # Execute all happy-path transitions
        machine.transition(meeting, "booking_detected")
        machine.transition(meeting, "invite_accepted")
        clock.set(datetime(2026, 6, 14, 14, 0, 0))
        machine.transition(meeting, "timer_t_minus_24h")
        clock.set(datetime(2026, 6, 15, 8, 0, 0))
        machine.transition(meeting, "timer_morning_of")

        # Check: no job has status written_back
        for job in machine.jobs:
            assert job.get("status") != JobStatus.WRITTEN_BACK.value, (
                f"Job {job['job_type']} reached written_back without approval!"
            )

    def test_emitted_jobs_start_awaiting_approval(self):
        """Customer-facing jobs should start in AWAITING_APPROVAL status."""
        clock = FakeClock()
        machine = ShowRateMachine(clock=clock)
        meeting = _make_meeting()

        job = machine.transition(meeting, "booking_detected")
        assert job is not None
        assert job["status"] == JobStatus.AWAITING_APPROVAL.value
        assert job["customer_facing"] is True
        assert job["approval_required"] is True

    def test_all_transitions_produce_gated_jobs(self):
        """Every customer-facing job in a full lifecycle is gated."""
        clock = FakeClock(start=datetime(2026, 6, 10, 9, 0, 0))
        machine = ShowRateMachine(clock=clock)

        meeting = _make_meeting(
            meeting_start=datetime(2026, 6, 17, 14, 0, 0),  # 7 days out for pull-in
        )

        # Pull-in check
        machine.check_pull_in(meeting)

        # Happy path
        machine.transition(meeting, "booking_detected")
        machine.transition(meeting, "invite_accepted")

        clock.set(datetime(2026, 6, 16, 14, 0, 0))
        machine.transition(meeting, "timer_t_minus_24h")

        clock.set(datetime(2026, 6, 17, 8, 0, 0))
        machine.transition(meeting, "timer_morning_of")

        customer_facing_jobs = [j for j in machine.jobs if j.get("customer_facing")]
        assert len(customer_facing_jobs) >= 3  # invite, 24h confirm, AM confirm

        for job in customer_facing_jobs:
            assert job["approval_required"] is True
            assert job["status"] == JobStatus.AWAITING_APPROVAL.value
