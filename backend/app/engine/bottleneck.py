"""Rule 5 — Bottleneck heuristic.

v1 priority order (DATA_MODEL.md):
  1. show_rate down >10pts vs baseline → hold-stage jobs first
  2. any positive_reply event unactioned >4h → convert-stage first (speed-to-book)
  3. else create-stage per Plan volumes

Returns stage priority + human-readable reason string.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.engine.types import (
    BottleneckResult,
    Event,
    EventType,
    FunnelStage,
    RateMetric,
    RateRow,
)


def identify_bottleneck(
    rates: list[RateRow],
    events: list[Event],
    *,
    as_of: datetime | None = None,
) -> BottleneckResult:
    """Rule 5: determine where to focus tomorrow's effort.

    Priority order is strict — rule 1 beats rule 2, rule 2 beats rule 3.
    """
    now = as_of or datetime.utcnow()

    # ── Rule 5.1: show_rate down >10pts vs baseline → hold ────────
    show_drift = _show_rate_drift(rates)
    if show_drift is not None and show_drift < -0.10:
        return BottleneckResult(
            stage=FunnelStage.HOLD,
            priority=1,
            reason=(
                f"Show rate {show_drift:+.0%} vs baseline — "
                "hold-stage jobs first to recover meetings"
            ),
        )

    # ── Rule 5.2: positive reply unactioned >4h → convert ─────────
    stale_positive = _has_stale_positive_reply(events, now)
    if stale_positive:
        return BottleneckResult(
            stage=FunnelStage.CONVERT,
            priority=2,
            reason="Positive reply unactioned >4h — convert-stage first (speed-to-book)",
        )

    # ── Rule 5.3: else → create ───────────────────────────────────
    return BottleneckResult(
        stage=FunnelStage.CREATE,
        priority=3,
        reason="No bottleneck detected — create-stage per Plan volumes",
    )


def _show_rate_drift(rates: list[RateRow]) -> float | None:
    """Return show_rate blended − baseline_90d, or None if no baseline."""
    for r in rates:
        if r.metric == RateMetric.SHOW_RATE and r.channel is None:
            if r.baseline_90d is not None:
                return r.blended_rate - r.baseline_90d
    return None


def _has_stale_positive_reply(events: list[Event], now: datetime) -> bool:
    """True if any positive_reply event older than 4h has no subsequent meeting_booked
    for the same (account, contact)."""
    cutoff = now - timedelta(hours=4)

    positive_keys: dict[tuple[str, str | None], datetime] = {}
    booked_keys: set[tuple[str, str | None]] = set()

    for e in events:
        key = (e.account_ref, e.contact_ref)
        if e.event_type == EventType.POSITIVE_REPLY:
            if key not in positive_keys or e.occurred_at > positive_keys[key]:
                positive_keys[key] = e.occurred_at
        elif e.event_type == EventType.MEETING_BOOKED:
            booked_keys.add(key)

    for key, ts in positive_keys.items():
        if key not in booked_keys and ts < cutoff:
            return True
    return False
