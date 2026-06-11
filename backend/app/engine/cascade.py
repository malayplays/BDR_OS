"""Rule 3 — Weekly cascade: goal ÷ rates → Plan.

Pure function, no I/O.  Runs in POINTS (not meetings).

Algorithm (DATA_MODEL.md Rule 3):
    remaining_pts    = goal.target_pts − credited − pending
    avg_pts_per_held = Σ(persona_mix_target_i × points_i × ad_accept_rate)
    weekly_held      = (remaining_pts / remaining_weeks) / avg_pts_per_held
    bookings_needed  = weekly_held / show_rate
    positives_needed = bookings_needed / book_rate
    touches_c        = positives_needed × mix_c / (reply_rate_c × positive_reply_rate)

Channel mix starts ⅓/⅓/⅓, shifts toward higher-converting channels, bounded 15%–60%.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta

from app.engine.rates import get_blended_rate
from app.engine.types import (
    DIALS_PER_HOUR,
    PERSONA_POINTS,
    Capacity,
    Channel,
    DailyAllocation,
    Goal,
    PersonaTier,
    Plan,
    RateMetric,
    RateRow,
    ReplanReason,
)

MIX_MIN = 0.15
MIX_MAX = 0.60


def _business_days_between(start: date, end: date) -> list[date]:
    """Return list of business days in [start, end]."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _remaining_weeks(
    period_end: date, capacity: Capacity, as_of: date
) -> float:
    """Business days remaining ÷ 5 (one work-week)."""
    bdays = _business_days_between(as_of, period_end)
    pto_set = set(capacity.pto_dates)
    available = [d for d in bdays if d not in pto_set]
    return max(len(available) / 5.0, 0.2)  # floor to avoid div-by-zero


def _compute_channel_mix(
    rates: list[RateRow],
) -> dict[str, float]:
    """Shift mix toward higher reply×positive×book channels, bounded 15%–60%."""
    channels = list(Channel)
    scores: dict[str, float] = {}

    for ch in channels:
        reply = get_blended_rate(rates, RateMetric.REPLY_RATE, ch)
        positive = get_blended_rate(rates, RateMetric.POSITIVE_REPLY_RATE, None)
        book = get_blended_rate(rates, RateMetric.BOOK_RATE, None)
        scores[ch] = reply * positive * book

    total = sum(scores.values())
    if total == 0:
        return {ch: 1.0 / len(channels) for ch in channels}

    raw_mix = {ch: scores[ch] / total for ch in channels}
    return _clamp_mix(raw_mix)


def _clamp_mix(mix: dict[str, float]) -> dict[str, float]:
    """Enforce 15%–60% bounds while summing to 1.0.

    Strategy: iteratively clamp out-of-bounds channels and redistribute
    their surplus/deficit to unclamped ("free") channels, preserving the
    relative proportions of the free channels.  Converges in ≤ N iterations
    where N = number of channels.
    """
    clamped = dict(mix)
    locked: set[str] = set()

    for _ in range(len(clamped) + 5):
        # Find channels that violate bounds
        newly_locked = False
        for ch in clamped:
            if ch in locked:
                continue
            if clamped[ch] < MIX_MIN:
                clamped[ch] = MIX_MIN
                locked.add(ch)
                newly_locked = True
            elif clamped[ch] > MIX_MAX:
                clamped[ch] = MIX_MAX
                locked.add(ch)
                newly_locked = True

        if not newly_locked:
            break

        # Redistribute: free channels absorb whatever remains
        locked_total = sum(clamped[ch] for ch in locked)
        free = [ch for ch in clamped if ch not in locked]
        if not free:
            break

        remaining = 1.0 - locked_total
        # Proportional redistribution among free channels
        free_raw = {ch: mix[ch] for ch in free}
        free_raw_total = sum(free_raw.values())
        if free_raw_total > 0:
            for ch in free:
                clamped[ch] = remaining * (free_raw[ch] / free_raw_total)
        else:
            per = remaining / len(free)
            for ch in free:
                clamped[ch] = per

    return clamped


def _avg_pts_per_held(
    persona_mix_target: dict[str, float] | None = None,
    ad_accept_rate: float = 0.90,
) -> float:
    """Expected points per held meeting given persona mix.

    Default mix if none provided: favor VP+ per COMP_MODEL.md §5.
    """
    if persona_mix_target is None:
        persona_mix_target = {
            PersonaTier.VP_LEVEL: 0.40,
            PersonaTier.DIRECTOR: 0.30,
            PersonaTier.MANAGER: 0.20,
            PersonaTier.IC: 0.10,
        }
    total = 0.0
    for tier, fraction in persona_mix_target.items():
        pts = PERSONA_POINTS.get(tier, 1.0)
        total += fraction * pts * ad_accept_rate
    return max(total, 0.01)


def compute_plan(
    goal: Goal,
    rates: list[RateRow],
    capacity: Capacity,
    *,
    credited_pts: float = 0.0,
    pending_pts: float = 0.0,
    as_of: date | None = None,
    persona_mix_target: dict[str, float] | None = None,
    replan_reason: str = ReplanReason.WEEKLY_CASCADE,
) -> Plan:
    """Rule 3: cascade goal into weekly/daily plan."""
    now = as_of or date.today()
    week_start = now - timedelta(days=now.weekday())  # Monday

    remaining_pts = max(goal.target_value - credited_pts - pending_pts, 0.0)

    ad_accept = get_blended_rate(rates, RateMetric.AD_ACCEPT_RATE, None)
    avg_pts = _avg_pts_per_held(persona_mix_target, ad_accept)

    rem_weeks = _remaining_weeks(goal.period_end, capacity, now)

    weekly_held = remaining_pts / rem_weeks / avg_pts
    show_rate = get_blended_rate(rates, RateMetric.SHOW_RATE, None)
    book_rate = get_blended_rate(rates, RateMetric.BOOK_RATE, None)
    positive_rate = get_blended_rate(rates, RateMetric.POSITIVE_REPLY_RATE, None)

    bookings_needed = weekly_held / max(show_rate, 0.01)
    positives_needed = bookings_needed / max(book_rate, 0.01)

    channel_mix = _compute_channel_mix(rates)

    # Per-channel touches
    touches_by_channel: dict[str, float] = {}
    for ch in Channel:
        reply_rate = get_blended_rate(rates, RateMetric.REPLY_RATE, ch)
        denom = max(reply_rate * positive_rate, 1e-6)
        touches_by_channel[ch] = positives_needed * channel_mix[ch] / denom

    # Spread across business days net of capacity
    pto_set = set(capacity.pto_dates)
    period_end = goal.period_end
    biz_days = _business_days_between(week_start, min(week_start + timedelta(days=6), period_end))
    available_days = [d for d in biz_days if d not in pto_set]
    n_days = max(len(available_days), 1)

    daily_allocs: list[DailyAllocation] = []
    for d in available_days:
        calls_per_day = touches_by_channel.get(Channel.CALL, 0.0) / n_days
        # Call-block sizing: calls ÷ dials_per_hour
        call_hours = calls_per_day / DIALS_PER_HOUR
        # Simple block: one call block starting at 10:00
        call_blocks: tuple[dict, ...] = ()
        if call_hours > 0:
            call_blocks = ({"start": "10:00", "end": f"{10 + math.ceil(call_hours)}:00"},)

        daily_allocs.append(
            DailyAllocation(
                day=d,
                email_touches=touches_by_channel.get(Channel.EMAIL, 0.0) / n_days,
                calls=calls_per_day,
                linkedin_touches=touches_by_channel.get(Channel.LINKEDIN, 0.0) / n_days,
                call_blocks=call_blocks,
                confirmations_due=0,
            )
        )

    rates_snapshot = {
        "show_rate": show_rate,
        "book_rate": book_rate,
        "positive_reply_rate": positive_rate,
        "ad_accept_rate": ad_accept,
        "channel_mix": channel_mix,
        "reply_rates": {ch: get_blended_rate(rates, RateMetric.REPLY_RATE, ch) for ch in Channel},
    }

    return Plan(
        id=str(uuid.uuid4()),
        goal_id=goal.id,
        week_start=week_start,
        weekly_bookings_required=bookings_needed,
        weekly_held_target=weekly_held,
        daily_allocations=tuple(daily_allocs),
        rates_snapshot=rates_snapshot,
        capacity=capacity,
        generated_at=datetime.utcnow(),
        replan_reason=replan_reason,
    )
