"""Rule 6 — Catch-up logic (when pace_gap < −0.15).

Ranked levers with estimated Δheld and attention cost:
  1. Pull in far-out booked meetings (>5 days) → hold-stage pull-in
  2. Revive stalled positive threads + recent closed-lost → convert-stage
  3. Shift channel mix toward highest-converting (within bounds)
  4. Raise raw volume — daily inflation capped at +25% over Plan

Session 1b additions:
  5. Dormancy-requalification batch (120-day list)
  6. Persona-mix shift up-market
  7. Month-end accelerator awareness (annotation only)
  8. M2 cap-awareness flag (surplus banking suggestion is advisory, never auto)

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
DORMANCY_THRESHOLD_DAYS = 120


def compute_catchup(
    funnel: FunnelState,
    plan: Plan,
    *,
    far_out_bookings: int = 0,
    stalled_positives: int = 0,
    cold_start: bool = False,
    dormant_contacts: int = 0,
    current_persona_mix_ic_pct: float = 0.0,
    month: int = 4,
    month_to_date_pts: float = 0.0,
    quota: float = 35.0,
    ramp_cap_pct: float | None = None,
) -> CatchupPlan:
    """Rule 6: propose ranked levers to close the pace gap.

    Args:
        funnel: current FunnelState (must have pace_gap < -0.15)
        plan: active Plan
        far_out_bookings: count of booked meetings >5 days out (pull-in candidates)
        stalled_positives: count of stalled positive threads
        cold_start: if True, levers are advisory-only (no auto job creation)
        dormant_contacts: count of contacts dormant ≥120 days (requalification candidates)
        current_persona_mix_ic_pct: current IC fraction of persona mix (for upmarket shift)
        month: current ramp month (for M2 cap awareness)
        month_to_date_pts: points earned so far this month
        quota: monthly quota
        ramp_cap_pct: if set (e.g. 2.0 for M2), commission cap multiplier

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
        attention_cost_hours=actual_inflation * 8,
        stage=FunnelStage.CREATE,
    ))
    remaining_to_close -= actual_delta

    # ── Lever 5 (Session 1b): Dormancy-requalification batch ─────
    if dormant_contacts > 0:
        ad_accept = plan.rates_snapshot.get("ad_accept_rate", 0.90)
        show_rate = plan.rates_snapshot.get("show_rate", 0.70)
        # Estimate: dormant contacts requalify as net-new, ~10% re-engage rate
        re_engage_rate = 0.10
        delta = dormant_contacts * re_engage_rate * show_rate * ad_accept
        levers.append(CatchupLever(
            name="dormancy_requalification",
            description=(
                f"Batch requalify {dormant_contacts} dormant contacts "
                f"(≥{DORMANCY_THRESHOLD_DAYS}d) as net-new"
            ),
            estimated_delta_held=round(delta, 2),
            attention_cost_hours=dormant_contacts * 0.05,
            stage=FunnelStage.CREATE,
        ))

    # ── Lever 6 (Session 1b): Persona-mix shift up-market ────────
    if current_persona_mix_ic_pct > 0.15:
        shift_improvement = plan.weekly_held_target * 0.10
        levers.append(CatchupLever(
            name="persona_mix_shift_upmarket",
            description=(
                f"Shift persona mix up-market (IC currently {current_persona_mix_ic_pct:.0%}, "
                f"target VP+/Director)"
            ),
            estimated_delta_held=round(shift_improvement, 2),
            attention_cost_hours=1.0,
            stage=FunnelStage.CREATE,
        ))

    # ── Lever 7 (Session 1b): Month-end accelerator awareness ────
    accelerator_annotation = None
    if month_to_date_pts >= quota:
        accelerator_annotation = (
            f"Month-end accelerator active: points above {quota} earn $100/pt "
            f"(currently {month_to_date_pts - quota:.1f} pts in accelerator zone)"
        )
        levers.append(CatchupLever(
            name="accelerator_awareness",
            description=accelerator_annotation,
            estimated_delta_held=0.0,
            attention_cost_hours=0.0,
            stage=FunnelStage.CREATE,
        ))

    # ── Lever 8 (Session 1b): M2 cap-awareness flag ──────────────
    surplus_banking_note = None
    if ramp_cap_pct is not None:
        cap_pts = quota * ramp_cap_pct
        if month_to_date_pts >= cap_pts:
            surplus = month_to_date_pts - cap_pts
            surplus_banking_note = (
                f"M{month} cap reached ({cap_pts:.0f} pts). "
                f"{surplus:.1f} surplus pts — consider slipping bookings to month+1 "
                f"(advisory only, never auto)"
            )
            levers.append(CatchupLever(
                name="m2_cap_awareness",
                description=surplus_banking_note,
                estimated_delta_held=0.0,
                attention_cost_hours=0.0,
                stage=FunnelStage.CREATE,
            ))

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
