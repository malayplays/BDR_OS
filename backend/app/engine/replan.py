"""Rule 4 — Trigger replans.

Checked on every event + hourly.  Fire cascade off-cycle when any of:
  (a) |pace_gap| > 0.15
  (b) any |blended − baseline_90d| > 0.10 absolute
  (c) Goal.edited_at changed
  (d) capacity change (PTO/holiday added)

Debounce: max one auto-replan per 24h per reason.
Cold-start: widened thresholds (pace 0.15 → 0.25, drift 0.10 → 0.15).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.engine.types import (
    Capacity,
    FunnelState,
    Goal,
    RateRow,
    ReplanReason,
    ReplanTrigger,
)

# Normal thresholds
_PACE_THRESHOLD = 0.15
_DRIFT_THRESHOLD = 0.10

# Cold-start widened thresholds (Rule 7)
_PACE_THRESHOLD_COLD = 0.25
_DRIFT_THRESHOLD_COLD = 0.15


def check_triggers(
    funnel: FunnelState,
    rates: list[RateRow],
    goal: Goal,
    current_capacity: Capacity,
    previous_capacity: Capacity | None = None,
    *,
    last_replans: dict[str, datetime] | None = None,
    cold_start: bool = False,
    as_of: datetime | None = None,
) -> list[ReplanTrigger]:
    """Evaluate all four replan conditions, respecting 24h debounce.

    Returns list of triggers that should fire (empty = no replan needed).
    """
    now = as_of or datetime.utcnow()
    debounce = last_replans or {}
    pace_thresh = _PACE_THRESHOLD_COLD if cold_start else _PACE_THRESHOLD
    drift_thresh = _DRIFT_THRESHOLD_COLD if cold_start else _DRIFT_THRESHOLD

    triggers: list[ReplanTrigger] = []

    # (a) Pace gap
    if abs(funnel.pace_gap) > pace_thresh:
        if _not_debounced(ReplanReason.PACE_GAP, debounce, now):
            triggers.append(ReplanTrigger(
                reason=ReplanReason.PACE_GAP,
                detail=f"pace_gap={funnel.pace_gap:+.2f} exceeds ±{pace_thresh}",
                fired_at=now,
            ))

    # (b) Rate drift
    for r in rates:
        if r.baseline_90d is not None:
            drift = abs(r.blended_rate - r.baseline_90d)
            if drift > drift_thresh:
                if _not_debounced(ReplanReason.RATE_DRIFT, debounce, now):
                    triggers.append(ReplanTrigger(
                        reason=ReplanReason.RATE_DRIFT,
                        detail=f"{r.metric}(ch={r.channel}) drift={drift:.3f} > {drift_thresh}",
                        fired_at=now,
                    ))
                break  # one trigger per reason

    # (c) Goal edited
    # Compare goal.edited_at against last cascade; if changed, fire.
    if _not_debounced(ReplanReason.GOAL_EDITED, debounce, now):
        last_goal_trigger = debounce.get(ReplanReason.GOAL_EDITED)
        if last_goal_trigger is None or goal.edited_at > last_goal_trigger:
            triggers.append(ReplanTrigger(
                reason=ReplanReason.GOAL_EDITED,
                detail=f"goal edited at {goal.edited_at.isoformat()}",
                fired_at=now,
            ))

    # (d) Capacity change
    if previous_capacity is not None and current_capacity != previous_capacity:
        if _not_debounced(ReplanReason.CAPACITY_CHANGE, debounce, now):
            triggers.append(ReplanTrigger(
                reason=ReplanReason.CAPACITY_CHANGE,
                detail="capacity changed (PTO/holiday update)",
                fired_at=now,
            ))

    return triggers


def _not_debounced(
    reason: str,
    last_replans: dict[str, datetime],
    now: datetime,
) -> bool:
    """True if this reason hasn't fired in the last 24h."""
    last = last_replans.get(reason)
    if last is None:
        return True
    return (now - last) > timedelta(hours=24)
