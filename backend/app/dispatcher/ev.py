"""Expected-value formulas for job types — all math in Δpoints.

EV = P(reply) × P(positive|reply) × P(book|positive) × P(show|booked) × persona_points × P(ad_accept)

Reference values from DATA_MODEL.md Object 6:
  EV(outreach_draft, VP)      = .04 × .35 × .55 × .70 × 5 × .9 ≈ 0.024
  EV(outreach_draft, IC)      ≈ 0.0035
  EV(book_response, VP pos)   = .55 × .70 × 5 × .9 ≈ 1.73
  EV(confirmation_24h, VP)    ≈ 0.10 × 5 × .9 ≈ 0.45
  EV(no_show_recovery, VP)    ≈ 0.25 × 5 × .9 ≈ 1.1
"""

from __future__ import annotations

from app.engine.points import MEETING_POINTS
from app.engine.types import (
    SEED_BENCHMARKS,
    PersonaTier,
    RateMetric,
    RateRow,
)


def _get_rate(rates: list[RateRow] | None, metric: str, channel: str | None = None) -> float:
    """Look up blended rate from live rates, falling back to seed benchmarks."""
    if rates:
        for r in rates:
            if r.metric == metric and r.channel == channel:
                return r.blended_rate
    return SEED_BENCHMARKS.get((metric, channel), 0.0)


def _get_rate_persona(
    rates: list[RateRow] | None,
    metric: str,
    persona_tier: str,
    channel: str | None = None,
) -> float:
    """Look up blended rate for a specific persona tier, falling back to non-persona rate."""
    if rates:
        for r in rates:
            if r.metric == metric and r.persona_tier == persona_tier and r.channel == channel:
                return r.blended_rate
    return _get_rate(rates, metric, channel)


def persona_points(tier: str) -> float:
    return MEETING_POINTS.get(tier, 0.0)


def ev_outreach(
    persona_tier: str,
    channel: str,
    rates: list[RateRow] | None = None,
) -> float:
    """EV of a cold outreach touch for a given persona × channel.

    EV = reply_rate(channel) × positive_reply_rate × book_rate × show_rate × persona_pts × ad_accept_rate
    """
    reply = _get_rate(rates, RateMetric.REPLY_RATE, channel)
    positive = _get_rate(rates, RateMetric.POSITIVE_REPLY_RATE)
    book = _get_rate(rates, RateMetric.BOOK_RATE)
    show = _get_rate(rates, RateMetric.SHOW_RATE)
    ad_accept = _get_rate(rates, RateMetric.AD_ACCEPT_RATE)
    pts = persona_points(persona_tier)
    return reply * positive * book * show * pts * ad_accept


def ev_book_response(
    persona_tier: str,
    rates: list[RateRow] | None = None,
) -> float:
    """EV of a book_response job (positive reply already received).

    EV = book_rate × show_rate × persona_pts × ad_accept_rate
    """
    book = _get_rate(rates, RateMetric.BOOK_RATE)
    show = _get_rate(rates, RateMetric.SHOW_RATE)
    ad_accept = _get_rate(rates, RateMetric.AD_ACCEPT_RATE)
    pts = persona_points(persona_tier)
    return book * show * pts * ad_accept


def ev_confirmation(
    persona_tier: str,
    rates: list[RateRow] | None = None,
    *,
    marginal_show_lift: float = 0.10,
) -> float:
    """EV of a confirmation (24h or AM) — marginal show-rate improvement.

    EV = marginal_show_lift × persona_pts × ad_accept_rate
    """
    ad_accept = _get_rate(rates, RateMetric.AD_ACCEPT_RATE)
    pts = persona_points(persona_tier)
    return marginal_show_lift * pts * ad_accept


def ev_no_show_recovery(
    persona_tier: str,
    rates: list[RateRow] | None = None,
    *,
    recovery_rate: float = 0.25,
) -> float:
    """EV of a no-show recovery job.

    EV = recovery_rate × persona_pts × ad_accept_rate
    """
    ad_accept = _get_rate(rates, RateMetric.AD_ACCEPT_RATE)
    pts = persona_points(persona_tier)
    return recovery_rate * pts * ad_accept


def ev_hold_generic(
    persona_tier: str,
    rates: list[RateRow] | None = None,
    *,
    marginal_lift: float = 0.05,
) -> float:
    """EV of a generic hold-stage job (reconfirm, pull_in, reschedule).

    Conservative: small marginal lift on show rate.
    """
    ad_accept = _get_rate(rates, RateMetric.AD_ACCEPT_RATE)
    pts = persona_points(persona_tier)
    return marginal_lift * pts * ad_accept


def ev_dormancy_requalify(
    persona_tier: str,
    rates: list[RateRow] | None = None,
    *,
    re_engage_rate: float = 0.10,
) -> float:
    """EV of a dormancy requalification job — the 120-day goldmine.

    High because re-engaged contacts at existing accounts qualify as net-new.
    """
    book = _get_rate(rates, RateMetric.BOOK_RATE)
    show = _get_rate(rates, RateMetric.SHOW_RATE)
    ad_accept = _get_rate(rates, RateMetric.AD_ACCEPT_RATE)
    pts = persona_points(persona_tier)
    return re_engage_rate * book * show * pts * ad_accept


# Map job_type → EV calculator. Each returns (ev, funnel_stage).
JOB_TYPE_TO_STAGE: dict[str, str] = {
    "research_brief": "create",
    "outreach_draft": "create",
    "reply_triage": "convert",
    "book_response": "convert",
    "confirmation_24h": "hold",
    "confirmation_am": "hold",
    "reconfirm": "hold",
    "pull_in_offer": "hold",
    "reschedule": "hold",
    "no_show_recovery": "hold",
    "call_prep": "hold",
    "crm_scribe": "hold",
    "pipeline_hygiene_autofix": "create",
    "reporting_personal_recap": "create",
    "reporting_manager_update": "create",
    "dormancy_requalify": "create",
    "crm_note_log": "create",
}


def compute_ev(
    job_type: str,
    persona_tier: str | None = None,
    channel: str | None = None,
    rates: list[RateRow] | None = None,
) -> float:
    """Compute expected value for a job type given persona tier and live rates."""
    tier = persona_tier or PersonaTier.MANAGER

    if job_type == "outreach_draft":
        ch = channel or "email"
        return ev_outreach(tier, ch, rates)

    if job_type == "book_response":
        return ev_book_response(tier, rates)

    if job_type in ("confirmation_24h", "confirmation_am"):
        return ev_confirmation(tier, rates)

    if job_type == "no_show_recovery":
        return ev_no_show_recovery(tier, rates)

    if job_type in ("reconfirm", "pull_in_offer", "reschedule"):
        return ev_hold_generic(tier, rates)

    if job_type == "dormancy_requalify":
        return ev_dormancy_requalify(tier, rates)

    if job_type == "research_brief":
        ch = channel or "email"
        return ev_outreach(tier, ch, rates)

    # Internal / low-EV jobs
    return 0.001
