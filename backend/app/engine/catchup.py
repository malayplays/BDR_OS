"""Rule 6 — Catch-up logic (when pace_gap < −0.15).

Ranked levers with estimated Δheld and attention cost:
  1. Pull in far-out booked meetings (>5 days) → hold-stage pull-in
  2. Revive stalled positive threads + recent closed-lost → convert-stage
  3. Shift channel mix toward highest-converting (within bounds)
  4. Raise raw volume — daily inflation capped at +25% over Plan

If cap can't close the gap: set at_risk=true, surface honest math.
"""

from __future__ import annotations

from app.engine.types import (
    CatchupLever,
    CatchupPlan,
    FunnelStage,
    FunnelState,
    Plan,
)

MAX_INFLATION_PCT = 0.25


def compute_catchup(
    funnel: FunnelState,
    plan: Plan,
    *,
    far_out_bookings: int = 0,
    stalled_positives: int = 0,
    cold_start: bool = False,
) -> CatchupPlan:
    """Rule 6: propose ranked levers to close the pace gap.

    Args:
        funnel: current FunnelState (must have pace_gap < -0.15)
        plan: active Plan
        far_out_bookings: count of booked meetings >5 days out (pull-in candidates)
        stalled_positives: count of stalled positive threads
        cold_start: if True, levers are advisory-only (no auto job creation)

    Returns:
        CatchupPlan with ranked levers, capped inflation, and at_risk flag.
    """
    gap_held = _held_gap(funnel, plan)
    remaining_to_close = gap_held
    levers: list[CatchupLever] = []

    # ── Lever 1: Pull in far-out booked meetings ──────────────────
    if far_out_bookings > 0:
        show_rate = plan.rates_snapshot.get("show_rate", 0.70)
        delta = far_out_bookings * show_rate
        levers.append(CatchupLever(
            name="pull_in_meetings",
            description=f"Pull in {far_out_bookings} meetings booked >5 days out",
            estimated_delta_held=round(delta, 2),
            attention_cost_hours=far_out_bookings * 0.1,
            stage=FunnelStage.HOLD,
        ))
        remaining_to_close -= delta

    # ── Lever 2: Revive stalled positive threads ──────────────────
    if stalled_positives > 0:
        book_rate = plan.rates_snapshot.get("book_rate", 0.55)
        show_rate = plan.rates_snapshot.get("show_rate", 0.70)
        delta = stalled_positives * book_rate * show_rate
        levers.append(CatchupLever(
            name="revive_stalled",
            description=f"Revive {stalled_positives} stalled positive threads",
            estimated_delta_held=round(delta, 2),
            attention_cost_hours=stalled_positives * 0.25,
            stage=FunnelStage.CONVERT,
        ))
        remaining_to_close -= delta

    # ── Lever 3: Shift channel mix ────────────────────────────────
    # Modest improvement estimate
    mix_delta = plan.weekly_held_target * 0.05
    levers.append(CatchupLever(
        name="shift_channel_mix",
        description="Shift channel mix toward highest-converting channels",
        estimated_delta_held=round(mix_delta, 2),
        attention_cost_hours=0.5,
        stage=FunnelStage.CREATE,
    ))
    remaining_to_close -= mix_delta

    # ── Lever 4: Raise raw volume (capped at +25%) ───────────────
    needed_inflation = remaining_to_close / max(plan.weekly_held_target, 0.01)
    actual_inflation = min(needed_inflation, MAX_INFLATION_PCT)
    actual_delta = plan.weekly_held_target * max(actual_inflation, 0.0)

    levers.append(CatchupLever(
        name="raise_volume",
        description=f"Raise daily volume by {actual_inflation:+.0%} (cap {MAX_INFLATION_PCT:+.0%})",
        estimated_delta_held=round(actual_delta, 2),
        attention_cost_hours=actual_inflation * 8,  # proportional to increase
        stage=FunnelStage.CREATE,
    ))
    remaining_to_close -= actual_delta

    # ── At-risk determination ─────────────────────────────────────
    at_risk = remaining_to_close > 0
    shortfall = max(remaining_to_close, 0.0)

    shortfall_detail = ""
    if at_risk:
        shortfall_detail = (
            f"Even with +{MAX_INFLATION_PCT:.0%} daily inflation cap, "
            f"shortfall of {shortfall:.1f} held meetings remains. "
            f"Goal at risk — honest math: need {gap_held:.1f} incremental held, "
            f"levers recover {gap_held - shortfall:.1f}."
        )

    return CatchupPlan(
        levers=tuple(levers),
        daily_inflation_pct=actual_inflation,
        at_risk=at_risk,
        shortfall=round(shortfall, 2),
        shortfall_detail=shortfall_detail,
    )


def _held_gap(funnel: FunnelState, plan: Plan) -> float:
    """How many more held meetings are needed vs. plan pace."""
    gap_stage = funnel.gap_by_stage.get("hold", {})
    actual = gap_stage.get("actual_held", funnel.counts.held)
    expected = gap_stage.get("expected_held", 0)
    return max(expected - actual, 0.0)
