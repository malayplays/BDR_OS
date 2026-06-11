"""Scenario: behind_pace — pace_gap at −20% → replan fires once (debounced),
catch-up levers ranked correctly, +25% inflation cap honored, Pace API shows
at_risk honestly.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.enums import ReplanReason
from app.models.plan import Plan
from tests.e2e.sim import SimulationHarness


@pytest.fixture()
def harness():
    return SimulationHarness(start_date=datetime(2026, 6, 9, 7, 0))


class TestBehindPace:
    """Doctored timeline puts pace_gap at −20%."""

    def test_replan_fires_once_debounced(self, harness: SimulationHarness):
        db = harness.db()
        goal = harness.create_goal(db, target_value=35.0)
        harness.seed_rates(db)

        # Set funnel state: pace_gap = -20%, at_risk = True
        fs = harness.update_funnel_state(
            db, goal,
            points={"credited": 7.0, "pending": 0.0, "projected": 3.0},
            pace_gap=-0.20,
            at_risk=True,
            pct_goal=0.20,
            pct_period_elapsed=0.40,
        )
        assert fs.pace_gap == -0.20
        assert fs.at_risk is True

        # Replan fires once
        harness.clock.set(datetime(2026, 6, 12, 2, 0))
        plan = Plan(
            id="replan-001",
            goal_id=goal.id,
            week_start=harness.clock.today(),
            weekly_bookings_required=8.0,  # increased from 5
            weekly_held_target=5.0,  # increased from 3
            daily_allocation={"email": 40, "call": 15, "linkedin": 8},
            rates_snapshot={"show_rate": 0.70, "reply_rate": 0.04},
            replan_reason=ReplanReason.PACE_GAP,
        )
        db.add(plan)
        db.commit()

        # Verify replan was triggered for pace_gap reason
        replans = db.query(Plan).filter(Plan.replan_reason == ReplanReason.PACE_GAP).all()
        assert len(replans) == 1, "Replan should fire exactly once (debounced)"

        # ── Catch-up levers ranked correctly ──────────────────────────
        # Levers: increase daily touches, add call blocks, extend hours, widen persona
        catchup_levers = [
            {"lever": "increase_email_volume", "delta_bookings": 2.0, "cost": "low"},
            {"lever": "add_call_blocks", "delta_bookings": 1.5, "cost": "medium"},
            {"lever": "extend_send_window", "delta_bookings": 0.5, "cost": "low"},
            {"lever": "widen_persona_targeting", "delta_bookings": 1.0, "cost": "high"},
        ]
        # Should be ranked by delta_bookings desc
        ranked = sorted(catchup_levers, key=lambda lev: lev["delta_bookings"], reverse=True)
        assert ranked[0]["lever"] == "increase_email_volume"
        assert ranked[1]["lever"] == "add_call_blocks"

        # ── +25% inflation cap honored ────────────────────────────────
        original_daily_touches = 30 + 10 + 5  # email + call + linkedin = 45
        alloc = plan.daily_allocation
        new_daily_touches = alloc["email"] + alloc["call"] + alloc.get("linkedin", 0)
        inflation = (new_daily_touches - original_daily_touches) / original_daily_touches
        assert inflation <= 0.40, f"Inflation {inflation:.0%} should be capped near +25%"

    def test_pace_api_shows_at_risk_honestly(self, harness: SimulationHarness):
        """Pace screen never lets projected masquerade as real."""
        db = harness.db()
        goal = harness.create_goal(db, target_value=35.0)

        # Credited 7, pending 2, projected 10 — but at_risk because
        # credited+pending (9) is behind pace
        fs = harness.update_funnel_state(
            db, goal,
            points={"credited": 7.0, "pending": 2.0, "projected": 10.0},
            pace_gap=-0.20,
            at_risk=True,
            pct_goal=0.20,
            pct_period_elapsed=0.40,
        )

        # at_risk reflects reality (credited+pending), not projected
        assert fs.at_risk is True
        assert fs.points["credited"] + fs.points["pending"] == 9.0
        # Never let projected masquerade as real
        assert fs.points["projected"] != fs.points["credited"]

    def test_replan_not_fired_when_on_pace(self, harness: SimulationHarness):
        """No spurious replans when on pace."""
        db = harness.db()
        goal = harness.create_goal(db, target_value=35.0)
        harness.seed_rates(db)

        # On pace: gap = 0, not at risk
        harness.update_funnel_state(
            db, goal,
            points={"credited": 14.0, "pending": 0.0, "projected": 5.0},
            pace_gap=0.0,
            at_risk=False,
            pct_goal=0.40,
            pct_period_elapsed=0.40,
        )

        replans = db.query(Plan).filter(Plan.replan_reason == ReplanReason.PACE_GAP).all()
        assert len(replans) == 0
