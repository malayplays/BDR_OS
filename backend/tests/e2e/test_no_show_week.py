"""Scenario: no_show_week — scripted no-show → recovery → rebook → HELD.

COMP_MODEL.md §3: no-show = ZERO credit. Recovery re-earns on occurrence.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.enums import EventType, JobStatus
from app.models.event_log import EventLog
from app.models.job import Job
from app.models.meeting_state import MeetingState
from tests.e2e.sim import SimulationHarness, calc_points


@pytest.fixture()
def harness():
    return SimulationHarness(start_date=datetime(2026, 6, 9, 7, 0))


class TestNoShowWeek:
    """No-show → recovery 3-touch sequence → rebook → HELD → credit restored."""

    def test_no_show_recovery_rebook_held(self, harness: SimulationHarness):
        db = harness.db()
        harness.create_goal(db)
        harness.seed_rates(db)
        account_ref = "acct-005"
        contact_ref = "con-017"

        # ── Setup: Meeting booked and in show-rate machine ────────────
        harness.ingest_event(
            db, EventType.MEETING_BOOKED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
        )
        meeting = harness.create_meeting_record(
            db, account_ref=account_ref, contact_ref=contact_ref,
        )
        harness.advance_meeting(db, meeting, MeetingState.INVITE_SENT)
        harness.advance_meeting(db, meeting, MeetingState.ACCEPTED)
        harness.advance_meeting(db, meeting, MeetingState.CONFIRMED_24H)
        harness.advance_meeting(db, meeting, MeetingState.CONFIRMED_AM)

        # ── No-show event ─────────────────────────────────────────────
        harness.clock.advance_days(3)
        harness.ingest_event(
            db, EventType.MEETING_NO_SHOW, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
            points_value=0.0,  # ZERO credit on no-show
        )
        harness.advance_meeting(db, meeting, MeetingState.NO_SHOW)
        assert meeting.state == MeetingState.NO_SHOW

        # Verify no-show = zero credit
        no_show_events = db.query(EventLog).filter(
            EventLog.event_type == EventType.MEETING_NO_SHOW,
            EventLog.account_ref == account_ref,
        ).all()
        assert len(no_show_events) == 1
        assert no_show_events[0].points_value == 0.0

        # ── T+10min: No-show recovery agent ───────────────────────────
        harness.clock.advance_hours(0.17)  # ~10 min
        recovery_job = harness.create_job(
            db,
            job_type="no_show_recovery",
            agent="no_show_recovery",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
            input_payload={"meeting_id": meeting.id, "touch": "t_plus_10"},
        )
        recovery_output = harness.run_agent(db, recovery_job)
        assert "t_plus_10_draft" in recovery_output
        assert "sequence" in recovery_output
        assert len(recovery_output["sequence"]) == 3  # 3-touch sequence

        # Approve recovery (batched: 3-touch sequence approves as one unit)
        harness.clock.advance_hours(2)
        harness.approve_job(db, recovery_job)
        assert recovery_job.status == JobStatus.WRITTEN_BACK

        # ── Recovery touch 1 (+1d): value nudge ───────────────────────
        harness.clock.advance_days(1)
        touch1_job = harness.create_job(
            db,
            job_type="no_show_recovery",
            agent="no_show_recovery",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
            input_payload={"touch": "day_1_nudge"},
        )
        harness.run_agent(db, touch1_job)
        harness.approve_job(db, touch1_job)

        # ── Recovery touch 2 (+3d): call task ─────────────────────────
        harness.clock.advance_days(2)
        touch2_job = harness.create_job(
            db,
            job_type="no_show_recovery",
            agent="no_show_recovery",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
            input_payload={"touch": "day_3_call"},
        )
        harness.run_agent(db, touch2_job)
        harness.approve_job(db, touch2_job)

        # ── Prospect reschedules (reply kills remaining sequence) ─────
        harness.clock.advance_hours(4)
        harness.ingest_event(
            db, EventType.REPLY_RECEIVED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
        )
        harness.ingest_event(
            db, EventType.MEETING_RESCHEDULED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
        )

        # New meeting booked from reschedule
        harness.ingest_event(
            db, EventType.MEETING_BOOKED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
        )
        harness.advance_meeting(db, meeting, MeetingState.RESCHEDULING)

        # ── Rebooked meeting → HELD ───────────────────────────────────
        rebooked = harness.create_meeting_record(
            db, account_ref=account_ref, contact_ref=contact_ref,
        )
        harness.advance_meeting(db, rebooked, MeetingState.INVITE_SENT)
        harness.advance_meeting(db, rebooked, MeetingState.ACCEPTED)
        harness.advance_meeting(db, rebooked, MeetingState.CONFIRMED_24H)
        harness.advance_meeting(db, rebooked, MeetingState.CONFIRMED_AM)

        harness.clock.advance_days(3)
        harness.ingest_event(
            db, EventType.MEETING_HELD, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
            points_value=calc_points("vp_level"),
        )
        harness.advance_meeting(db, rebooked, MeetingState.HELD)
        assert rebooked.state == MeetingState.HELD

        # ── Assertions ────────────────────────────────────────────────
        # Original no-show had zero credit
        assert no_show_events[0].points_value == 0.0

        # Rebooked meeting earns credit on occurrence
        held_events = db.query(EventLog).filter(
            EventLog.event_type == EventType.MEETING_HELD,
            EventLog.account_ref == account_ref,
        ).all()
        assert len(held_events) >= 1
        assert held_events[-1].points_value == 5.0  # VP = 5 pts

        # Recovery sequence created the right jobs
        recovery_jobs = db.query(Job).filter(
            Job.agent == "no_show_recovery",
            Job.account_ref == account_ref,
        ).all()
        assert len(recovery_jobs) >= 3

        # All recovery jobs were approved
        for j in recovery_jobs:
            assert j.approval is not None

    def test_no_show_zero_credit(self, harness: SimulationHarness):
        """Explicit: no-show meeting MUST have zero points."""
        db = harness.db()
        harness.ingest_event(
            db, EventType.MEETING_NO_SHOW, "acct-005",
            persona_tier="director", points_value=0.0,
        )
        ev = db.query(EventLog).filter(
            EventLog.event_type == EventType.MEETING_NO_SHOW
        ).first()
        assert ev.points_value == 0.0
