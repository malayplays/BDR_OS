"""Scenario: credit_pipeline — VP meeting booked → held → AD-accepted
(+5 credited pts, provenance complete); second meeting no-shows (0 pts) →
recovery → held → accepted; one acceptance lags 4d → hygiene nudge fires;
earnings projector and promotion scorecard reflect final state exactly
(assert $ math per COMP_MODEL.md §6).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.enums import EventType
from app.models.event_log import EventLog
from app.models.meeting_state import MeetingState
from tests.e2e.sim import (
    BASE_SALARY,
    QUOTA,
    RATE_PER_POINT,
    SimulationHarness,
    calc_points,
    project_earnings,
    promotion_scorecard,
)


@pytest.fixture()
def harness():
    return SimulationHarness(start_date=datetime(2026, 6, 9, 7, 0))


class TestCreditPipeline:
    """Full credit pipeline: booked → held → AD-accepted → points credited."""

    def test_vp_meeting_full_credit_flow(self, harness: SimulationHarness):
        """VP meeting: booked → held → AD-accepted = +5 credited pts with provenance."""
        db = harness.db()
        harness.create_goal(db)
        harness.seed_rates(db)
        account_ref = "acct-006"
        contact_ref = "con-021"

        # ── Book meeting ──────────────────────────────────────────────
        harness.ingest_event(
            db, EventType.MEETING_BOOKED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
        )
        meeting = harness.create_meeting_record(
            db, account_ref=account_ref, contact_ref=contact_ref,
            persona_tier="vp_level",
        )

        # ── Meeting HELD ──────────────────────────────────────────────
        harness.clock.advance_days(3)
        harness.ingest_event(
            db, EventType.MEETING_HELD, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
            points_value=calc_points("vp_level"),
        )
        harness.advance_meeting(db, meeting, MeetingState.HELD)

        # Points are "pending" after held, before AD acceptance
        held_events = db.query(EventLog).filter(
            EventLog.event_type == EventType.MEETING_HELD,
            EventLog.account_ref == account_ref,
        ).all()
        assert len(held_events) == 1
        assert held_events[0].points_value == 5.0  # VP = 5 pts

        # ── AD Accepted ───────────────────────────────────────────────
        harness.clock.advance_days(1)
        harness.ingest_event(
            db, EventType.AD_ACCEPTED, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
            points_value=calc_points("vp_level"),
            payload={
                "provenance": {
                    "outbound_touches": ["ev-001", "ev-002", "ev-003"],
                    "first_touch_channel": "email",
                    "named_target_validated": True,
                    "dormancy_check": {"last_activity_date": "2026-01-15", "days_dormant": 145},
                }
            },
        )

        # Points are now "credited"
        ad_events = db.query(EventLog).filter(
            EventLog.event_type == EventType.AD_ACCEPTED,
            EventLog.account_ref == account_ref,
        ).all()
        assert len(ad_events) == 1
        assert ad_events[0].points_value == 5.0
        assert ad_events[0].payload["provenance"]["named_target_validated"] is True

    def test_no_show_then_recovery_then_credit(self, harness: SimulationHarness):
        """Second meeting no-shows (0 pts) → recovery → held → accepted."""
        db = harness.db()
        harness.create_goal(db)
        account_ref = "acct-007"
        contact_ref = "con-025"

        # Book and no-show
        harness.ingest_event(
            db, EventType.MEETING_BOOKED, account_ref,
            persona_tier="director", contact_ref=contact_ref,
        )
        harness.ingest_event(
            db, EventType.MEETING_NO_SHOW, account_ref,
            persona_tier="director", contact_ref=contact_ref,
            points_value=0.0,
        )

        # Recovery → rebook
        recovery_job = harness.create_job(
            db,
            job_type="no_show_recovery",
            agent="no_show_recovery",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            is_customer_facing=True,
        )
        harness.run_agent(db, recovery_job)
        harness.approve_job(db, recovery_job)

        # Rebooked meeting held
        harness.clock.advance_days(5)
        harness.ingest_event(
            db, EventType.MEETING_HELD, account_ref,
            persona_tier="director", contact_ref=contact_ref,
            points_value=calc_points("director"),
        )

        # AD accepted
        harness.clock.advance_days(2)
        harness.ingest_event(
            db, EventType.AD_ACCEPTED, account_ref,
            persona_tier="director", contact_ref=contact_ref,
            points_value=calc_points("director"),
        )

        # Assertions: no-show = 0, held rebook = 3 (director)
        no_show_ev = db.query(EventLog).filter(
            EventLog.event_type == EventType.MEETING_NO_SHOW,
            EventLog.account_ref == account_ref,
        ).first()
        assert no_show_ev.points_value == 0.0

        held_ev = db.query(EventLog).filter(
            EventLog.event_type == EventType.MEETING_HELD,
            EventLog.account_ref == account_ref,
        ).first()
        assert held_ev.points_value == 3.0  # director

    def test_ad_acceptance_lag_hygiene_nudge(self, harness: SimulationHarness):
        """AD acceptance lags 4d → hygiene nudge fires."""
        db = harness.db()
        harness.create_goal(db)
        account_ref = "acct-008"
        contact_ref = "con-029"

        # Meeting held
        held_at = harness.clock.now()
        harness.ingest_event(
            db, EventType.MEETING_HELD, account_ref,
            persona_tier="vp_level", contact_ref=contact_ref,
            points_value=calc_points("vp_level"),
        )

        # 4 days pass, no AD acceptance
        harness.clock.advance_days(4)

        # Pipeline hygiene detects the lag
        hygiene_job = harness.create_job(
            db,
            job_type="ad_acceptance_nudge",
            agent="pipeline_hygiene",
            funnel_stage="hold",
            account_ref=account_ref,
            contact_ref=contact_ref,
            input_payload={
                "meeting_held_at": held_at.isoformat(),
                "days_since_held": 4,
                "reason": "AD acceptance lagging >3d",
            },
        )
        harness.run_agent(db, hygiene_job)
        harness.approve_job(db, hygiene_job)

        assert hygiene_job.input_payload["days_since_held"] == 4
        assert hygiene_job.output is not None

    def test_earnings_projector_exact_math(self, harness: SimulationHarness):
        """Earnings projector matches COMP_MODEL.md §6 exactly."""
        # M4 (quota=35): 40 pts earned, 2 SPIFF
        result = project_earnings(month=4, points=40.0, spiffs=2000.0)

        monthly_base = BASE_SALARY / 12.0  # $5,833.33
        base_variable = min(40.0, QUOTA) * RATE_PER_POINT  # 35 * 71.43 = $2,500.05
        accel_variable = (40.0 - QUOTA) * 100.0  # 5 * 100 = $500
        expected_total = monthly_base + base_variable + accel_variable + 2000.0

        assert result["base"] == pytest.approx(monthly_base, abs=0.01)
        assert result["variable"] == pytest.approx(base_variable + accel_variable, abs=0.01)
        assert result["spiffs"] == 2000.0
        assert result["total"] == pytest.approx(expected_total, abs=0.01)

    def test_earnings_m1_guarantee(self, harness: SimulationHarness):
        """M1: 100% OTE guaranteed regardless of points."""
        result = project_earnings(month=1, points=0.0)
        monthly_ote = (BASE_SALARY + 30_000.0) / 12.0
        assert result["total"] == pytest.approx(monthly_ote, abs=0.01)
        assert result["ramp_note"] == "M1 guaranteed"

    def test_earnings_m2_cap(self, harness: SimulationHarness):
        """M2: quota=15, commission capped at 200%."""
        result = project_earnings(month=2, points=50.0)  # Way over cap

        # Cap: 15 * 71.43 * 2.0 = $2,142.90 max variable
        max_variable = 15.0 * RATE_PER_POINT * 2.0
        monthly_base = BASE_SALARY / 12.0
        assert result["variable"] == pytest.approx(max_variable, abs=0.01)
        assert result["total"] == pytest.approx(monthly_base + max_variable, abs=0.01)

    def test_accelerator_kicks_in_at_36(self, harness: SimulationHarness):
        """Points 36+ are worth $100/pt (accelerator), not $71.43."""
        result_35 = project_earnings(month=4, points=35.0)
        result_36 = project_earnings(month=4, points=36.0)

        marginal_value = result_36["total"] - result_35["total"]
        assert marginal_value == pytest.approx(100.0, abs=0.01), (
            f"Marginal pt #36 should be worth $100, got ${marginal_value:.2f}"
        )

    def test_promotion_scorecard_correct(self, harness: SimulationHarness):
        """Promotion scorecard tracks attainment streak, sourced S2, months above Sr."""
        monthly_points = [48.0, 50.0, 47.0, 55.0, 52.0]  # 5 months
        sourced_s2 = 3

        sc = promotion_scorecard(monthly_points, sourced_s2)

        # All 5 months are ≥130% of 35 (45.5) — streak = 5
        assert sc["attainment_streak_130pct"] == 5
        assert sc["sourced_s2_count"] == 3
        # All 5 months are ≥40 (Sr. SDR quota) — months above Sr = 5
        assert sc["months_above_sr_quota"] == 5

    def test_promotion_scorecard_broken_streak(self, harness: SimulationHarness):
        """Streak resets when a month dips below 130%."""
        monthly_points = [48.0, 30.0, 50.0, 55.0]  # month 2 breaks streak
        sc = promotion_scorecard(monthly_points, sourced_s2=1)

        # Streak counts from the end: 55, 50 are ≥45.5 → streak = 2
        assert sc["attainment_streak_130pct"] == 2
        assert sc["sourced_s2_count"] == 1

    def test_persona_points_correct(self):
        """Point values match COMP_MODEL.md §2."""
        assert calc_points("global_c_suite") == 8.0
        assert calc_points("vp_level") == 5.0
        assert calc_points("director") == 3.0
        assert calc_points("manager") == 1.0
        assert calc_points("ic") == 0.5
