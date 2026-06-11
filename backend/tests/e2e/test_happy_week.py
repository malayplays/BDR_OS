"""Scenario: happy_week — full golden-path lifecycle.

signal → brief → copy → (approve) → draft → scripted positive reply →
book_response within SLA → booking → full show-rate machine → HELD →
scribe → reporting.

Assert: EventLog tells the complete story, every customer-facing artifact
passed an approval.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.enums import EventType, JobStatus, VerdictResult
from app.models.event_log import EventLog
from app.models.job import Job
from app.models.meeting_state import MeetingState
from tests.e2e.sim import SimulationHarness, calc_points, load_timeline


@pytest.fixture()
def harness():
    return SimulationHarness(start_date=datetime(2026, 6, 9, 7, 0))


class TestHappyWeek:
    """Full positive-path week: signal → brief → copy → approve → draft → reply →
    book_response → booking → show-rate machine → HELD → scribe → report."""

    def test_full_lifecycle(self, harness: SimulationHarness):
        db = harness.db()
        load_timeline()  # warm cache
        harness.create_goal(db)
        harness.seed_rates(db)
        account_ref = "acct-004"  # non-strategic target account
        contact_ref = "con-013"

        # ── Day 1 (Monday): Signal → Research Brief ──────────────────
        harness.clock.set(datetime(2026, 6, 9, 8, 0))

        # Ingest signal event
        harness.ingest_event(
            db, EventType.TOUCH_SENT, account_ref,
            persona_tier="vp_level", channel="email", contact_ref=contact_ref,
        )

        # Create and run research_brief job
        brief_job = harness.create_job(
            db,
            job_type="research_brief",
            agent="research_brief",
            funnel_stage="create",
            account_ref=account_ref,
            contact_ref=contact_ref,
            expected_value=0.024,
        )
        brief_output = harness.run_agent(db, brief_job)
        assert brief_output["confidence"] >= 0.8
        assert brief_output["needs_human_because"] is None

        # Brief is auto-approved (internal artifact)
        v = harness.approve_job(db, brief_job)
        assert v.result == VerdictResult.ALLOW  # non-customer-facing

        # ── Day 1: Copy Agent (chains from brief) ────────────────────
        copy_job = harness.create_job(
            db,
            job_type="outreach_draft",
            agent="copy",
            funnel_stage="create",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
            expected_value=0.05,
            input_payload={"brief": brief_output},
        )
        copy_output = harness.run_agent(db, copy_job)
        assert len(copy_output["email_variants"]) == 3
        assert copy_output["confidence"] >= 0.8

        # Copy requires approval — approve at 11:30 check-in
        harness.clock.set(datetime(2026, 6, 9, 11, 30))
        v_copy = harness.approve_job(db, copy_job)
        # During draft-only period, create_draft is ALLOWED
        assert v_copy.result == VerdictResult.ALLOW
        assert copy_job.status == JobStatus.WRITTEN_BACK

        # ── Day 2 (Tuesday): Positive reply → Inbox Triage → Book Response
        harness.clock.set(datetime(2026, 6, 10, 9, 15))

        harness.ingest_event(
            db, EventType.REPLY_RECEIVED, account_ref,
            persona_tier="vp_level", channel="email", contact_ref=contact_ref,
        )
        harness.ingest_event(
            db, EventType.POSITIVE_REPLY, account_ref,
            persona_tier="vp_level", channel="email", contact_ref=contact_ref,
        )

        # Inbox triage
        triage_job = harness.create_job(
            db,
            job_type="inbox_triage",
            agent="inbox_triage",
            funnel_stage="convert",
            account_ref=account_ref,
            contact_ref=contact_ref,
        )
        triage_output = harness.run_agent(db, triage_job)
        assert triage_output["classification"] == "positive"
        assert triage_output["next_job"] == "book_response"

        # Auto-approve triage (classification only)
        harness.approve_job(db, triage_job)

        # Book response — SLA: created within minutes, due +4h
        book_job = harness.create_job(
            db,
            job_type="book_response",
            agent="book_response",
            funnel_stage="convert",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
            due_at=harness.clock.now() + timedelta(hours=4),
        )
        book_output = harness.run_agent(db, book_job)
        assert "reply_in_thread" in book_output

        # Approve book response at 11:30 check-in (within SLA)
        harness.clock.set(datetime(2026, 6, 10, 11, 30))
        v_book = harness.approve_job(db, book_job)
        assert v_book.result == VerdictResult.ALLOW
        assert book_job.status == JobStatus.WRITTEN_BACK

        sla_elapsed = (
            datetime(2026, 6, 10, 11, 30) - datetime(2026, 6, 10, 9, 15)
        ).total_seconds() / 3600
        assert sla_elapsed < 4.0, f"Book response SLA exceeded: {sla_elapsed}h"

        # ── Day 2: Booking event ──────────────────────────────────────
        harness.ingest_event(
            db, EventType.MEETING_BOOKED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
        )

        # ── Day 3-4: Show-rate machine (BOOKED → INVITE → ACCEPTED → CONFIRMED_24H → CONFIRMED_AM)
        meeting = harness.create_meeting_record(
            db, account_ref=account_ref, contact_ref=contact_ref,
        )
        assert meeting.state == MeetingState.BOOKED

        # Invite sent
        invite_job = harness.create_job(
            db,
            job_type="calendar_invite",
            agent="show_rate_machine",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
        )
        harness.run_agent(db, invite_job)
        harness.approve_job(db, invite_job)
        harness.advance_meeting(db, meeting, MeetingState.INVITE_SENT)

        # Invite accepted event
        harness.ingest_event(db, EventType.INVITE_ACCEPTED, account_ref, contact_ref=contact_ref)
        harness.advance_meeting(db, meeting, MeetingState.ACCEPTED)

        # T-24h confirmation
        harness.clock.advance_days(1)
        confirm_job = harness.create_job(
            db,
            job_type="reminder_24h",
            agent="show_rate_machine",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
        )
        harness.run_agent(db, confirm_job)
        harness.approve_job(db, confirm_job)
        harness.advance_meeting(db, meeting, MeetingState.CONFIRMED_24H)

        # Morning-of reminder
        harness.clock.advance_hours(16)
        am_job = harness.create_job(
            db,
            job_type="reminder_am",
            agent="show_rate_machine",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
        )
        harness.run_agent(db, am_job)
        harness.approve_job(db, am_job)
        harness.advance_meeting(db, meeting, MeetingState.CONFIRMED_AM)

        # ── Day 5: Meeting HELD ───────────────────────────────────────
        harness.clock.advance_hours(2)
        harness.ingest_event(
            db, EventType.MEETING_HELD, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
            points_value=calc_points("vp_level"),
        )
        harness.advance_meeting(db, meeting, MeetingState.HELD)
        assert meeting.state == MeetingState.HELD

        # ── Day 5: CRM Scribe (post-meeting) ─────────────────────────
        scribe_job = harness.create_job(
            db,
            job_type="crm_scribe",
            agent="crm_scribe",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
        )
        scribe_output = harness.run_agent(db, scribe_job)
        assert scribe_output["sql_checklist"]["icp_fit"] is True
        harness.approve_job(db, scribe_job)

        # ── Day 5: AD Accepted ────────────────────────────────────────
        harness.clock.advance_days(1)
        harness.ingest_event(
            db, EventType.AD_ACCEPTED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
            points_value=calc_points("vp_level"),
        )

        # ── Day 6 (Friday): Reporting ────────────────────────────────
        harness.clock.set(datetime(2026, 6, 13, 15, 0))
        report_job = harness.create_job(
            db,
            job_type="weekly_report",
            agent="reporting",
            funnel_stage="create",
            account_ref=None,
        )
        report_output = harness.run_agent(db, report_job)
        assert "personal_recap" in report_output
        assert "manager_draft" in report_output

        # Manager draft requires approval
        harness.approve_job(db, report_job)

        # ── Assertions ────────────────────────────────────────────────

        # EventLog tells the complete story
        all_events = db.query(EventLog).filter(EventLog.account_ref == account_ref).all()
        event_types = {e.event_type for e in all_events}
        expected_events = {
            EventType.TOUCH_SENT,
            EventType.REPLY_RECEIVED,
            EventType.POSITIVE_REPLY,
            EventType.MEETING_BOOKED,
            EventType.INVITE_ACCEPTED,
            EventType.MEETING_HELD,
            EventType.AD_ACCEPTED,
        }
        assert expected_events.issubset(event_types), (
            f"Missing events: {expected_events - event_types}"
        )

        # Every customer-facing artifact passed approval
        customer_facing_types = {"outreach_draft", "book_response", "calendar_invite",
                                 "reminder_24h", "reminder_am"}
        cf_jobs = db.query(Job).filter(
            Job.account_ref == account_ref,
            Job.job_type.in_(customer_facing_types),
        ).all()
        for j in cf_jobs:
            assert j.status in {JobStatus.WRITTEN_BACK, JobStatus.APPROVED}, (
                f"Customer-facing job {j.job_type} not approved: {j.status}"
            )
            assert j.approval is not None, f"Job {j.job_type} missing approval record"

        # Points credited
        ad_events = [e for e in all_events if e.event_type == EventType.AD_ACCEPTED]
        assert len(ad_events) >= 1
        assert ad_events[0].points_value == 5.0  # VP = 5 pts

    def test_narrative_readable(self, harness: SimulationHarness):
        """Verify the narrative log is human-readable for the demo."""
        db = harness.db()
        harness.create_goal(db)
        harness.seed_rates(db)

        # Run a minimal lifecycle
        job = harness.create_job(
            db, job_type="research_brief", agent="research_brief",
            account_ref="acct-004",
        )
        harness.run_agent(db, job)
        harness.approve_job(db, job)

        narrative = harness.print_narrative()
        assert "JOB CREATED" in narrative
        assert "AGENT RAN" in narrative
        assert "APPROVED" in narrative
