"""Earnings projector per COMP_MODEL.md §6.

Ramp-aware (M1 guarantee, M2 200% cap), $71.43/pt → $100/pt accelerator split,
SPIFF cash, Sr.-rate switch on promotion date, monthly + annualized vs $135k goal,
marginal-$ of next point.

Pure functions, zero I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Comp plan constants from COMP_MODEL.md §1 ─────────────────────────

SDR_BASE_ANNUAL = 70_000.0
SDR_VARIABLE_ANNUAL = 30_000.0
SDR_OTE = SDR_BASE_ANNUAL + SDR_VARIABLE_ANNUAL  # $100k

SR_SDR_BASE_ANNUAL = 75_000.0
SR_SDR_VARIABLE_ANNUAL = 35_000.0
SR_SDR_OTE = SR_SDR_BASE_ANNUAL + SR_SDR_VARIABLE_ANNUAL  # $110k

SDR_QUOTA = 35.0
SR_SDR_QUOTA = 40.0

SDR_RATE_PER_POINT = 71.43     # $71.43/pt up to quota
SR_SDR_RATE_PER_POINT = 72.92  # Sr. rate
ACCELERATOR_RATE = 100.0       # $100/pt above quota

PERSONAL_GOAL_ANNUAL = 135_000.0

# Ramp schedule: month -> (quota, cap_pct or None)
# M1=0 (100% OTE guaranteed), M2=15 (200% cap), M3=30, M4+=35
RAMP_SCHEDULE: dict[int, tuple[float, float | None]] = {
    1: (0.0, None),      # M1: full OTE guaranteed
    2: (15.0, 2.0),      # M2: quota 15, capped 200% of variable/12
    3: (30.0, None),      # M3: quota 30
}
# M4+ default to SDR_QUOTA (35)


@dataclass(frozen=True)
class EarningsProjection:
    base_monthly: float
    commission: float
    spiff_cash: float
    total_monthly: float
    annualized: float
    vs_goal_annual: float
    marginal_dollar_next_point: float
    month: int
    quota: float
    points: float
    is_promoted: bool
    cap_applied: bool = False


def _get_ramp(month: int, *, is_promoted: bool) -> tuple[float, float | None]:
    """Return (quota, cap_pct_or_None) for a given ramp month."""
    if is_promoted:
        return SR_SDR_QUOTA, None
    if month in RAMP_SCHEDULE:
        return RAMP_SCHEDULE[month]
    return SDR_QUOTA, None


def project_earnings(
    points: float,
    month: int,
    *,
    spiff_cash: float = 0.0,
    is_promoted: bool = False,
    promotion_month: int | None = None,
) -> EarningsProjection:
    """Compute monthly earnings projection per COMP_MODEL.md §6.

    project(month) = base/12 + min(pts, quota)×rate + max(pts−quota,0)×100 + spiffs
    Ramp-aware: M1 guarantee, M2 cap.
    """
    # Determine if promoted as of this month
    effective_promoted = is_promoted or (promotion_month is not None and month >= promotion_month)

    if effective_promoted:
        base_annual = SR_SDR_BASE_ANNUAL
        ote = SR_SDR_OTE
        rate_per_point = SR_SDR_RATE_PER_POINT
    else:
        base_annual = SDR_BASE_ANNUAL
        ote = SDR_OTE
        rate_per_point = SDR_RATE_PER_POINT

    base_monthly = base_annual / 12.0

    quota, cap_pct = _get_ramp(month, is_promoted=effective_promoted)

    # M1: full OTE guaranteed (commission = variable/12 regardless)
    if month == 1 and not effective_promoted:
        variable_monthly = (ote - base_annual) / 12.0
        return EarningsProjection(
            base_monthly=base_monthly,
            commission=variable_monthly,
            spiff_cash=spiff_cash,
            total_monthly=base_monthly + variable_monthly + spiff_cash,
            annualized=(base_monthly + variable_monthly + spiff_cash) * 12,
            vs_goal_annual=(base_monthly + variable_monthly + spiff_cash) * 12 - PERSONAL_GOAL_ANNUAL,
            marginal_dollar_next_point=0.0,
            month=month,
            quota=quota,
            points=points,
            is_promoted=effective_promoted,
        )

    # Commission calculation
    base_commission = min(points, quota) * rate_per_point
    accelerator_commission = max(points - quota, 0.0) * ACCELERATOR_RATE
    commission = base_commission + accelerator_commission

    # M2 cap: 200% of monthly variable
    cap_applied = False
    if cap_pct is not None:
        variable_monthly = (ote - base_annual) / 12.0
        cap_value = cap_pct * variable_monthly
        if commission > cap_value:
            commission = cap_value
            cap_applied = True

    total_monthly = base_monthly + commission + spiff_cash
    annualized = total_monthly * 12

    # Marginal $ of next point
    if cap_applied:
        marginal = 0.0  # capped out
    elif points >= quota:
        marginal = ACCELERATOR_RATE
    else:
        marginal = rate_per_point

    return EarningsProjection(
        base_monthly=round(base_monthly, 2),
        commission=round(commission, 2),
        spiff_cash=round(spiff_cash, 2),
        total_monthly=round(total_monthly, 2),
        annualized=round(annualized, 2),
        vs_goal_annual=round(annualized - PERSONAL_GOAL_ANNUAL, 2),
        marginal_dollar_next_point=round(marginal, 2),
        month=month,
        quota=quota,
        points=points,
        is_promoted=effective_promoted,
        cap_applied=cap_applied,
    )
