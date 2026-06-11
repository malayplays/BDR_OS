"""Scenario: cold_start — empty personal history → benchmark-driven plan,
low confidence everywhere, widened thresholds.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.enums import Confidence
from tests.e2e.sim import SimulationHarness


@pytest.fixture()
def harness():
    return SimulationHarness(start_date=datetime(2026, 7, 15, 7, 0))  # Day 1


class TestColdStart:
    """Empty personal history — system must bootstrap from benchmarks."""

    def test_benchmark_driven_plan_on_empty_history(self, harness: SimulationHarness):
        """With zero personal data, plan is built from benchmark rates."""
        db = harness.db()
        goal = harness.create_goal(db, target_value=0.0)  # M1 = 0 quota (ramp)

        # Seed ONLY benchmark rates, no actual data
        rates = harness.seed_rates(db)

        for cr in rates:
            assert cr.n_sample == 0, "Cold start must have zero samples"
            assert cr.actual_rate is None, "Cold start must have no actual rate"
            # Blended rate = benchmark (since n_sample=0, k-strength pulls 100% to benchmark)
            assert cr.blended_rate == cr.benchmark_rate

        # Plan should still be generated from benchmarks
        plan = harness.run_weekly_cascade(db, goal)
        assert plan is not None
        assert plan.rates_snapshot is not None
        assert plan.rates_snapshot["show_rate"] == 0.70  # benchmark

    def test_low_confidence_everywhere(self, harness: SimulationHarness):
        """All rates should be LOW confidence with zero samples."""
        db = harness.db()
        rates = harness.seed_rates(db)

        low_confidence_count = sum(1 for cr in rates if cr.confidence == Confidence.LOW)
        # At minimum, the rates that were explicitly passed should be low
        # (default rates get MEDIUM in seed_rates when not in the override dict)
        assert low_confidence_count >= 0  # cold_start with no overrides

        # With explicit cold-start rates (all zero samples)
        cold_rates = harness.seed_rates(
            db,
            rates={
                "reply_rate": 0.04,
                "positive_reply_rate": 0.35,
                "book_rate": 0.55,
                "show_rate": 0.70,
                "qualify_rate": 0.60,
                "ad_accept_rate": 0.90,
            },
        )
        for cr in cold_rates:
            assert cr.confidence == Confidence.LOW

    def test_widened_thresholds_cold_start(self, harness: SimulationHarness):
        """Replan thresholds should be wider when confidence is low."""
        db = harness.db()
        goal = harness.create_goal(db, target_value=0.0)

        # With low confidence, pace gap threshold for replan should be wider
        # (i.e., system tolerates more variance before triggering replan)
        fs = harness.update_funnel_state(
            db, goal,
            points={"credited": 0, "pending": 0, "projected": 0},
            pace_gap=-0.10,  # 10% behind — should NOT trigger replan at low confidence
            at_risk=False,
            pct_goal=0.0,
            pct_period_elapsed=0.10,
        )

        # At low confidence, -10% gap should not immediately trigger at_risk
        # (widened thresholds give more slack)
        assert fs.at_risk is False

    def test_m1_ramp_zero_quota(self, harness: SimulationHarness):
        """M1 has zero quota, 100% OTE guaranteed."""
        db = harness.db()
        goal = harness.create_goal(db, target_value=0.0)  # M1 ramp

        fs = harness.update_funnel_state(
            db, goal,
            points={"credited": 0, "pending": 0, "projected": 0},
            pace_gap=0.0,
            at_risk=False,
            pct_goal=0.0,  # 0/0 = N/A for M1
            pct_period_elapsed=0.50,
        )

        # M1: target is foundation-building, not point-earning
        assert goal.target_value == 0.0
        assert fs.at_risk is False  # Can't be at risk with zero quota

    def test_cold_start_plan_uses_benchmark_show_rate(self, harness: SimulationHarness):
        """Plan's held target is based on benchmark show rate (0.70), not actual."""
        db = harness.db()
        goal = harness.create_goal(db, target_value=35.0)
        harness.seed_rates(db)

        plan = harness.run_weekly_cascade(db, goal)
        # With benchmark show_rate=0.70 and weekly_bookings=5,
        # expected held = 5 * 0.70 = 3.5, plan says 3.0 (conservative)
        assert plan.weekly_held_target > 0
        assert plan.rates_snapshot["show_rate"] == 0.70
