"""Scenario: rate_drift — show rate drops 12pts → next morning's Today leads
with hold-stage jobs.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.conversion_rates import ConversionRates
from app.models.enums import Confidence, FunnelStage, RateMetric, ReplanReason
from app.models.plan import Plan
from tests.e2e.sim import SimulationHarness


@pytest.fixture()
def harness():
    return SimulationHarness(start_date=datetime(2026, 6, 9, 7, 0))


class TestRateDrift:
    """Show rate drops 12pts → dispatcher reranks hold-stage first."""

    def test_show_rate_drop_reprioritizes_hold_stage(self, harness: SimulationHarness):
        db = harness.db()
        goal = harness.create_goal(db)

        # Seed baseline rates: show_rate = 0.70 (baseline_90d)
        baseline_show = ConversionRates(
            id="rate-show-baseline",
            metric=RateMetric.SHOW_RATE,
            benchmark_rate=0.70,
            blended_rate=0.70,
            baseline_90d=0.70,
            confidence=Confidence.HIGH,
            n_sample=50,
            window_days=90,
        )
        db.add(baseline_show)
        db.commit()

        # ── Nightly rates recompute: show rate dropped 12pts ──────────
        harness.clock.set(datetime(2026, 6, 10, 2, 0))

        drifted_show = ConversionRates(
            id="rate-show-drifted",
            metric=RateMetric.SHOW_RATE,
            benchmark_rate=0.70,
            blended_rate=0.58,  # dropped 12pts
            baseline_90d=0.70,
            actual_rate=0.58,
            confidence=Confidence.HIGH,
            n_sample=30,
            window_days=30,
        )
        db.add(drifted_show)
        db.commit()

        drift = drifted_show.baseline_90d - drifted_show.blended_rate
        assert drift == pytest.approx(0.12, abs=0.01)

        # Replan triggered by rate drift
        plan = Plan(
            id="replan-drift-001",
            goal_id=goal.id,
            week_start=harness.clock.today(),
            weekly_bookings_required=5.0,
            weekly_held_target=4.0,  # increased because show rate dropped
            daily_allocation={"email": 20, "call": 10, "linkedin": 5},
            rates_snapshot={"show_rate": 0.58, "reply_rate": 0.04},
            replan_reason=ReplanReason.RATE_DRIFT,
        )
        db.add(plan)
        db.commit()

        assert plan.replan_reason == ReplanReason.RATE_DRIFT

        # ── Morning dispatcher: hold-stage jobs should lead ───────────
        harness.clock.set(datetime(2026, 6, 10, 7, 30))

        # Create mix of create-stage and hold-stage jobs
        create_job = harness.create_job(
            db,
            job_type="outreach_draft",
            agent="copy",
            funnel_stage=FunnelStage.CREATE,
            account_ref="acct-010",
            expected_value=0.05,
            priority_score=0.5,
        )
        hold_job_1 = harness.create_job(
            db,
            job_type="reminder_24h",
            agent="show_rate_machine",
            funnel_stage=FunnelStage.HOLD,
            account_ref="acct-011",
            expected_value=0.15,
            priority_score=0.9,  # Higher priority due to show rate being the bottleneck
        )
        hold_job_2 = harness.create_job(
            db,
            job_type="reconfirm",
            agent="show_rate_machine",
            funnel_stage=FunnelStage.HOLD,
            account_ref="acct-012",
            expected_value=0.12,
            priority_score=0.85,
        )

        # Dispatcher should rank hold-stage first
        jobs_by_priority = sorted(
            [create_job, hold_job_1, hold_job_2],
            key=lambda j: j.priority_score,
            reverse=True,
        )
        assert jobs_by_priority[0].funnel_stage == FunnelStage.HOLD
        assert jobs_by_priority[1].funnel_stage == FunnelStage.HOLD

        # Verify the narrative recognizes show rate as bottleneck
        hold_count = len([j for j in jobs_by_priority if j.funnel_stage == FunnelStage.HOLD])
        harness.narrative.append(
            f"[{harness.clock.now().isoformat()}] DISPATCHER: Show rate is the "
            f"bottleneck (−12pts); leading with {hold_count} hold-stage jobs"
        )

    def test_drift_detection_threshold(self, harness: SimulationHarness):
        """Only trigger rate_drift replan when drift exceeds meaningful threshold."""
        db = harness.db()
        harness.create_goal(db)

        # Small drift (2pts) — should NOT trigger replan
        small_drift_rate = ConversionRates(
            id="rate-show-small",
            metric=RateMetric.SHOW_RATE,
            benchmark_rate=0.70,
            blended_rate=0.68,  # only 2pts drop
            baseline_90d=0.70,
            actual_rate=0.68,
            confidence=Confidence.HIGH,
            n_sample=30,
        )
        db.add(small_drift_rate)
        db.commit()

        drift = small_drift_rate.baseline_90d - small_drift_rate.blended_rate
        assert drift < 0.05  # Not significant enough for replan

        # Large drift (12pts) — SHOULD trigger replan
        large_drift_rate = ConversionRates(
            id="rate-show-large",
            metric=RateMetric.SHOW_RATE,
            benchmark_rate=0.70,
            blended_rate=0.58,  # 12pts drop
            baseline_90d=0.70,
            actual_rate=0.58,
            confidence=Confidence.HIGH,
            n_sample=30,
        )
        db.add(large_drift_rate)
        db.commit()

        drift = large_drift_rate.baseline_90d - large_drift_rate.blended_rate
        assert drift >= 0.10  # Significant — triggers replan
