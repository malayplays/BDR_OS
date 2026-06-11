"""Scheduler hooks — thin glue calling engine pure functions.

Nightly rates recompute 02:00, Sunday cascade 21:00, hourly trigger check.
APScheduler wiring; actual engine logic lives in engine/.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.engine.types import Capacity, Event, FunnelState, Goal, RateRow, ReplanReason


def nightly_rates_job(
    get_events: Callable[[], list[Event]],
    store_rates: Callable[[list[RateRow]], None],
    *,
    cold_start: bool = False,
) -> list[RateRow]:
    """02:00 nightly — recompute all conversion rates."""
    from app.engine.rates import compute_rates

    events = get_events()
    rates = compute_rates(events, datetime.utcnow(), cold_start=cold_start)
    store_rates(rates)
    return rates


def weekly_cascade_job(
    get_goal: Callable[[], Goal],
    get_rates: Callable[[], list[RateRow]],
    get_capacity: Callable[[], Capacity],
    get_points: Callable[[], tuple[float, float]],
    store_plan: Callable, # type: ignore[type-arg]
) -> None:
    """Sunday 21:00 — run weekly cascade to produce a new Plan."""
    from app.engine.cascade import compute_plan

    goal = get_goal()
    rates = get_rates()
    capacity = get_capacity()
    credited, pending = get_points()

    plan = compute_plan(
        goal,
        rates,
        capacity,
        credited_pts=credited,
        pending_pts=pending,
        replan_reason=ReplanReason.WEEKLY_CASCADE,
    )
    store_plan(plan)


def hourly_trigger_check_job(
    get_funnel: Callable[[], FunnelState],
    get_rates: Callable[[], list[RateRow]],
    get_goal: Callable[[], Goal],
    get_capacity: Callable[[], tuple[Capacity, Capacity | None]],
    get_last_replans: Callable[[], dict[str, datetime]],
    fire_replan: Callable, # type: ignore[type-arg]
    *,
    cold_start: bool = False,
) -> None:
    """Hourly — check replan triggers, fire cascade if needed."""
    from app.engine.replan import check_triggers

    funnel = get_funnel()
    rates = get_rates()
    goal = get_goal()
    current_cap, prev_cap = get_capacity()
    last_replans = get_last_replans()

    triggers = check_triggers(
        funnel,
        rates,
        goal,
        current_cap,
        prev_cap,
        last_replans=last_replans,
        cold_start=cold_start,
    )

    if triggers:
        fire_replan(triggers)


def register_schedules(scheduler) -> None:  # type: ignore[no-untyped-def]
    """Register engine jobs on an APScheduler instance.

    This is the wiring point — the actual job implementations above
    accept dependency-injected callables so they remain testable.
    """
    # Placeholder: actual registration depends on app context / DI setup.
    # The scheduler object would be an APScheduler BackgroundScheduler.
    #
    # scheduler.add_job(nightly_rates_job, 'cron', hour=2, minute=0, ...)
    # scheduler.add_job(weekly_cascade_job, 'cron', day_of_week='sun', hour=21, ...)
    # scheduler.add_job(hourly_trigger_check_job, 'cron', minute=0, ...)
    pass
