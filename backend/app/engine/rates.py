"""Rule 2 — Rolling-window rate computation.

Pure functions: event list in → RateRow list out.  Zero I/O.

Blend formula:  rate = (actual × n + benchmark × k) / (n + k)
Confidence:     n < 20 → low; 20–75 → medium; > 75 → high
Cold-start:     k = 60, force confidence = low  (Rule 7)
90-day baseline: same blend formula over 90-day window for drift detection.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.engine.types import (
    SEED_BENCHMARKS,
    Channel,
    Confidence,
    Event,
    EventType,
    RateMetric,
    RateRow,
)


def _confidence(n: int, *, cold_start: bool = False) -> str:
    if cold_start:
        return Confidence.LOW
    if n < 20:
        return Confidence.LOW
    if n <= 75:
        return Confidence.MEDIUM
    return Confidence.HIGH


def blend(actual: float | None, n: int, benchmark: float, k: int) -> float:
    """Bayesian blend: (actual×n + benchmark×k) / (n+k)."""
    if actual is None or n == 0:
        return benchmark
    return (actual * n + benchmark * k) / (n + k)


def _count_events(
    events: list[Event],
    event_type: str,
    *,
    channel: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
) -> int:
    count = 0
    for e in events:
        if e.event_type != event_type:
            continue
        if channel is not None and e.channel != channel:
            continue
        if after is not None and e.occurred_at <= after:
            continue
        if before is not None and e.occurred_at > before:
            continue
        count += 1
    return count


def _compute_rate_pair(
    numerator_type: str,
    denominator_type: str,
    events: list[Event],
    *,
    channel: str | None = None,
    after: datetime,
    before: datetime,
) -> tuple[float | None, int]:
    """Return (actual_rate, denominator_count) for a window."""
    denom = _count_events(events, denominator_type, channel=channel, after=after, before=before)
    if denom == 0:
        return None, 0
    numer = _count_events(events, numerator_type, channel=channel, after=after, before=before)
    return numer / denom, denom


# Metric → (numerator_event_type, denominator_event_type, per_channel)
_METRIC_DEFS: dict[str, tuple[str, str, bool]] = {
    RateMetric.REPLY_RATE: (EventType.REPLY_RECEIVED, EventType.TOUCH_SENT, True),
    RateMetric.POSITIVE_REPLY_RATE: (EventType.POSITIVE_REPLY, EventType.REPLY_RECEIVED, False),
    RateMetric.BOOK_RATE: (EventType.MEETING_BOOKED, EventType.POSITIVE_REPLY, False),
    RateMetric.SHOW_RATE: (EventType.MEETING_HELD, EventType.MEETING_BOOKED, False),
    RateMetric.QUALIFY_RATE: (EventType.AD_ACCEPTED, EventType.MEETING_HELD, False),
    RateMetric.AD_ACCEPT_RATE: (EventType.AD_ACCEPTED, EventType.MEETING_HELD, False),
}


def compute_rates(
    events: list[Event],
    as_of: datetime,
    *,
    window_days: int = 30,
    cold_start: bool = False,
    k_override: int | None = None,
) -> list[RateRow]:
    """Compute all rate rows for the given event stream as of a point in time.

    Returns one RateRow per (metric, channel) combination.
    """
    k_default = 60 if cold_start else 30
    k = k_override if k_override is not None else k_default

    window_start = as_of - timedelta(days=window_days)
    baseline_start = as_of - timedelta(days=90)

    rows: list[RateRow] = []

    for metric, (num_type, denom_type, per_channel) in _METRIC_DEFS.items():
        channels: list[str | None] = list(Channel) if per_channel else [None]

        for ch in channels:
            benchmark = SEED_BENCHMARKS.get((metric, ch))
            if benchmark is None:
                benchmark = SEED_BENCHMARKS.get((metric, None), 0.0)

            actual, n = _compute_rate_pair(
                num_type, denom_type, events, channel=ch, after=window_start, before=as_of
            )

            blended = blend(actual, n, benchmark, k)

            # 90-day baseline
            actual_90, n_90 = _compute_rate_pair(
                num_type, denom_type, events, channel=ch, after=baseline_start, before=as_of
            )
            baseline_90d = blend(actual_90, n_90, benchmark, k) if n_90 > 0 else None

            rows.append(
                RateRow(
                    metric=metric,
                    channel=ch,
                    window_days=window_days,
                    n_sample=n,
                    actual_rate=actual,
                    benchmark_rate=benchmark,
                    k_strength=k,
                    blended_rate=blended,
                    confidence=_confidence(n, cold_start=cold_start),
                    baseline_90d=baseline_90d,
                    computed_at=as_of,
                )
            )

    return rows


def get_blended_rate(
    rates: list[RateRow], metric: str, channel: str | None = None
) -> float:
    """Look up a single blended rate from a rates list."""
    for r in rates:
        if r.metric == metric and r.channel == channel:
            return r.blended_rate
    # Fallback to benchmark
    return SEED_BENCHMARKS.get((metric, channel), SEED_BENCHMARKS.get((metric, None), 0.0))
