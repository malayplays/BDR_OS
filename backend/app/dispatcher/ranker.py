"""Ranker — scores and sorts jobs for the Today list.

priority_score = (expected_value / estimated_minutes) × urgency_boost

Urgency boost:
  - due within 4h:  ×3
  - overdue:        ×5

Stage-gated by bottleneck.py output:
  Rule 5 is a GATE, not a weight — the bottleneck stage's jobs sort above
  all others regardless of score.

IC demotion (COMP_MODEL.md §5):
  IC-persona create-jobs require explicit override to rank above Manager+.
"""

from __future__ import annotations

from datetime import datetime

from app.dispatcher.job_factory import estimated_minutes
from app.engine.types import BottleneckResult, PersonaTier


def urgency_boost(due_at: datetime | None, now: datetime | None = None) -> float:
    """Compute urgency multiplier based on due_at proximity."""
    if due_at is None:
        return 1.0
    _now = now or datetime.utcnow()
    remaining = (due_at - _now).total_seconds()
    if remaining < 0:
        return 5.0  # overdue
    if remaining < 4 * 3600:
        return 3.0  # due within 4h
    return 1.0


def compute_priority_score(
    expected_value: float,
    est_minutes: int,
    due_at: datetime | None = None,
    now: datetime | None = None,
) -> float:
    """priority_score = EV / est_minutes × urgency_boost."""
    if est_minutes <= 0:
        est_minutes = 1
    base = expected_value / est_minutes
    boost = urgency_boost(due_at, now)
    return base * boost


def rank_jobs(
    jobs: list[dict],
    bottleneck: BottleneckResult,
    *,
    now: datetime | None = None,
    ic_override_ids: set[str] | None = None,
) -> list[dict]:
    """Sort jobs by stage gate then priority_score.

    1. Compute priority_score for each job
    2. Apply IC demotion: IC create-jobs move behind Manager+ unless overridden
    3. Stage-gate: bottleneck stage's jobs sort above all others
    4. Within each tier, sort by priority_score descending
    """
    _now = now or datetime.utcnow()
    overrides = ic_override_ids or set()

    for j in jobs:
        ev = j.get("expected_value", 0.0)
        est = j.get("estimated_minutes") or estimated_minutes(j["job_type"])
        j["priority_score"] = compute_priority_score(ev, est, j.get("due_at"), _now)

    def sort_key(j: dict) -> tuple[int, int, float]:
        # Tier 0 = bottleneck stage, Tier 1 = other stages
        is_bottleneck = 0 if j.get("funnel_stage") == bottleneck.stage else 1

        # IC demotion: IC create-jobs get demoted (tier 2) unless overridden
        ic_demoted = 0
        persona = _extract_persona(j)
        if (
            persona == PersonaTier.IC
            and j.get("funnel_stage") == "create"
            and j.get("id", "") not in overrides
        ):
            ic_demoted = 1

        return (is_bottleneck, ic_demoted, -j.get("priority_score", 0.0))

    return sorted(jobs, key=sort_key)


def _extract_persona(job: dict) -> str | None:
    """Extract persona tier from job payload or trigger."""
    payload = job.get("input_payload") or {}
    if "persona_tier" in payload:
        return payload["persona_tier"]
    trigger = job.get("trigger") or {}
    return trigger.get("persona_tier")
