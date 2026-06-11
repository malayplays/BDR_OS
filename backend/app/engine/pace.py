"""Rule 1 — Event-driven FunnelState derivation.

Pure function: (events, goal, plan?, as_of) → FunnelState.  Zero I/O.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.engine.types import (
    PERSONA_POINTS,
    Channel,
    Event,
    EventType,
    FunnelCounts,
    FunnelState,
    Goal,
    Plan,
    PointsBucket,
)


def _business_days_between(start: date, end: date) -> int:
    count = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count


def _count_touches(events: list[Event], channel: str) -> int:
    return sum(
        1 for e in events
        if e.event_type == EventType.TOUCH_SENT and e.channel == channel
    )


def compute_funnel_state(
    events: list[Event],
    goal: Goal,
    *,
    plan: Plan | None = None,
    as_of: datetime | None = None,
    ad_accept_rate: float = 0.90,
    show_rate: float = 0.70,
) -> FunnelState:
    """Derive FunnelState from event log + goal.

    Counts are period-to-date (events within goal.period_start..period_end).
    Points: credited (ad_accepted), pending (held, awaiting AD), projected (booked × show × accept).
    pct_goal = (credited + pending) / target.
    pace_gap = pct_goal − pct_period_elapsed.
    """
    now = as_of or datetime.utcnow()

    period_start_dt = datetime.combine(goal.period_start, datetime.min.time())
    period_end_dt = datetime.combine(goal.period_end, datetime.max.time())

    in_period = [
        e for e in events
        if period_start_dt <= e.occurred_at <= period_end_dt
    ]

    # ── counts ─────────────────────────────────────────────────────
    touches_email = _count_touches(in_period, Channel.EMAIL)
    touches_call = _count_touches(in_period, Channel.CALL)
    touches_linkedin = _count_touches(in_period, Channel.LINKEDIN)
    replies = sum(1 for e in in_period if e.event_type == EventType.REPLY_RECEIVED)
    positive_replies = sum(1 for e in in_period if e.event_type == EventType.POSITIVE_REPLY)
    booked = sum(1 for e in in_period if e.event_type == EventType.MEETING_BOOKED)
    held = sum(1 for e in in_period if e.event_type == EventType.MEETING_HELD)
    no_shows = sum(1 for e in in_period if e.event_type == EventType.MEETING_NO_SHOW)
    ad_accepted_count = sum(1 for e in in_period if e.event_type == EventType.AD_ACCEPTED)
    s1 = sum(1 for e in in_period if e.event_type == EventType.S1_REACHED)
    s2 = sum(1 for e in in_period if e.event_type == EventType.S2_REACHED)

    counts = FunnelCounts(
        touches_email=touches_email,
        touches_call=touches_call,
        touches_linkedin=touches_linkedin,
        replies=replies,
        positive_replies=positive_replies,
        booked=booked,
        held=held,
        no_shows=no_shows,
        ad_accepted=ad_accepted_count,
        s1=s1,
        s2=s2,
    )

    # ── points ─────────────────────────────────────────────────────
    credited = 0.0
    for e in in_period:
        if e.event_type == EventType.AD_ACCEPTED and e.points_value is not None:
            credited += e.points_value

    # pending: held meetings not yet AD-accepted → estimate using persona points
    held_events = [e for e in in_period if e.event_type == EventType.MEETING_HELD]
    accepted_contacts = {
        (e.account_ref, e.contact_ref)
        for e in in_period if e.event_type == EventType.AD_ACCEPTED
    }
    pending = 0.0
    for e in held_events:
        if (e.account_ref, e.contact_ref) not in accepted_contacts:
            pts = PERSONA_POINTS.get(e.persona_tier, 1.0) if e.persona_tier else 1.0
            pending += pts * ad_accept_rate

    # projected: booked but not yet held
    held_contacts = {
        (e.account_ref, e.contact_ref) for e in held_events
    }
    no_show_contacts = {
        (e.account_ref, e.contact_ref)
        for e in in_period if e.event_type == EventType.MEETING_NO_SHOW
    }
    projected = 0.0
    for e in in_period:
        if e.event_type == EventType.MEETING_BOOKED:
            key = (e.account_ref, e.contact_ref)
            if key not in held_contacts and key not in no_show_contacts:
                pts = PERSONA_POINTS.get(e.persona_tier, 1.0) if e.persona_tier else 1.0
                projected += pts * show_rate * ad_accept_rate

    points = PointsBucket(credited=credited, pending=pending, projected=projected)

    # ── persona mix ────────────────────────────────────────────────
    persona_mix: dict[str, int] = {}
    for e in in_period:
        if e.event_type in (EventType.MEETING_HELD, EventType.AD_ACCEPTED) and e.persona_tier:
            persona_mix[e.persona_tier] = persona_mix.get(e.persona_tier, 0) + 1

    # ── pace ───────────────────────────────────────────────────────
    total_bdays = _business_days_between(goal.period_start, goal.period_end)
    elapsed_bdays = _business_days_between(goal.period_start, min(now.date(), goal.period_end))
    pct_period_elapsed = elapsed_bdays / max(total_bdays, 1)
    pct_goal = (credited + pending) / max(goal.target_value, 0.01)
    pace_gap = pct_goal - pct_period_elapsed

    # ── gap_by_stage ───────────────────────────────────────────────
    gap_by_stage = _compute_gap_by_stage(counts, plan, pct_period_elapsed)

    return FunnelState(
        goal_id=goal.id,
        as_of=now,
        counts=counts,
        points=points,
        persona_mix=persona_mix,
        pct_goal=pct_goal,
        pct_period_elapsed=pct_period_elapsed,
        pace_gap=pace_gap,
        gap_by_stage=gap_by_stage,
    )


def _compute_gap_by_stage(
    counts: FunnelCounts,
    plan: Plan | None,
    pct_elapsed: float,
) -> dict:
    """Per-stage actual vs expected-at-this-point given current Plan."""
    if plan is None:
        return {}

    total_touches = counts.touches_email + counts.touches_call + counts.touches_linkedin
    weekly_touches = sum(
        a.email_touches + a.calls + a.linkedin_touches
        for a in plan.daily_allocations
    )

    expected_touches = weekly_touches * pct_elapsed * 4  # rough monthly
    expected_bookings = plan.weekly_bookings_required * pct_elapsed * 4
    expected_held = plan.weekly_held_target * pct_elapsed * 4

    return {
        "create": {
            "actual_touches": total_touches,
            "expected_touches": round(expected_touches, 1),
            "gap": total_touches - expected_touches,
        },
        "convert": {
            "actual_booked": counts.booked,
            "expected_booked": round(expected_bookings, 1),
            "gap": counts.booked - expected_bookings,
        },
        "hold": {
            "actual_held": counts.held,
            "expected_held": round(expected_held, 1),
            "gap": counts.held - expected_held,
        },
    }
