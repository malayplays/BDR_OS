"""Morning plan — assembles the Today payload.

Trigger: 07:30 daily + on replan.

Output: ranked jobs, plan summary (touches by channel remaining, call blocks,
confirmations due), bottleneck narrative (template-based; LLM polish optional
behind a flag, default off).
"""

from __future__ import annotations

from datetime import datetime

from app.dispatcher.ranker import rank_jobs
from app.engine.bottleneck import identify_bottleneck
from app.engine.types import (
    BottleneckResult,
    Event,
    Plan,
    RateRow,
)


def build_today_payload(
    jobs: list[dict],
    plan: Plan | None,
    rates: list[RateRow],
    events: list[Event],
    *,
    now: datetime | None = None,
    ic_override_ids: set[str] | None = None,
    llm_polish: bool = False,
) -> dict:
    """Assemble the /api/today response payload.

    Returns:
        {
            "date": str,
            "bottleneck": {stage, reason},
            "ranked_jobs": [...],
            "plan_summary": {
                "touches_remaining": {email, call, linkedin},
                "call_blocks": [...],
                "confirmations_due": int,
            },
            "narrative": str,
        }
    """
    _now = now or datetime.utcnow()

    bottleneck = identify_bottleneck(rates, events, as_of=_now)
    ranked = rank_jobs(jobs, bottleneck, now=_now, ic_override_ids=ic_override_ids)

    plan_summary = _build_plan_summary(plan)
    narrative = _build_narrative(bottleneck, plan_summary, ranked, llm_polish=llm_polish)

    return {
        "date": _now.strftime("%Y-%m-%d"),
        "bottleneck": {
            "stage": bottleneck.stage,
            "priority": bottleneck.priority,
            "reason": bottleneck.reason,
        },
        "ranked_jobs": [_job_to_payload(j) for j in ranked],
        "plan_summary": plan_summary,
        "narrative": narrative,
    }


def _build_plan_summary(plan: Plan | None) -> dict:
    """Extract today's allocation from the plan."""
    if plan is None:
        return {
            "touches_remaining": {"email": 0, "call": 0, "linkedin": 0},
            "call_blocks": [],
            "confirmations_due": 0,
        }

    today_alloc = None
    today = datetime.utcnow().date()
    for alloc in plan.daily_allocations:
        if alloc.day == today:
            today_alloc = alloc
            break

    if today_alloc is None and plan.daily_allocations:
        today_alloc = plan.daily_allocations[0]

    if today_alloc:
        return {
            "touches_remaining": {
                "email": int(today_alloc.email_touches),
                "call": int(today_alloc.calls),
                "linkedin": int(today_alloc.linkedin_touches),
            },
            "call_blocks": [dict(b) for b in today_alloc.call_blocks],
            "confirmations_due": today_alloc.confirmations_due,
        }

    return {
        "touches_remaining": {"email": 0, "call": 0, "linkedin": 0},
        "call_blocks": [],
        "confirmations_due": 0,
    }


def _build_narrative(
    bottleneck: BottleneckResult,
    plan_summary: dict,
    ranked_jobs: list[dict],
    *,
    llm_polish: bool = False,
) -> str:
    """Template-based narrative. LLM polish is behind a flag (default off)."""
    stage_counts: dict[str, int] = {}
    for j in ranked_jobs:
        s = j.get("funnel_stage", "unknown")
        stage_counts[s] = stage_counts.get(s, 0) + 1

    touches = plan_summary.get("touches_remaining", {})
    total_touches = sum(touches.values())
    confirmations = plan_summary.get("confirmations_due", 0)

    parts = [f"Bottleneck: {bottleneck.reason}"]

    if stage_counts:
        breakdown = ", ".join(f"{count} {stage}" for stage, count in sorted(stage_counts.items()))
        parts.append(f"Today: {len(ranked_jobs)} jobs ({breakdown}).")

    if total_touches > 0:
        parts.append(
            f"Touches remaining: {touches.get('email', 0)} email, "
            f"{touches.get('call', 0)} call, {touches.get('linkedin', 0)} LinkedIn."
        )

    if confirmations > 0:
        parts.append(f"Confirmations due: {confirmations}.")

    return " ".join(parts)


def _job_to_payload(j: dict) -> dict:
    """Serialize a job dict for the API response."""
    return {
        "id": j.get("id", ""),
        "job_type": j["job_type"],
        "funnel_stage": j.get("funnel_stage", ""),
        "agent": j.get("agent", ""),
        "account_ref": j.get("account_ref"),
        "contact_ref": j.get("contact_ref"),
        "status": j.get("status", "pending"),
        "expected_value": j.get("expected_value", 0.0),
        "priority_score": j.get("priority_score", 0.0),
        "estimated_minutes": j.get("estimated_minutes", 0),
        "due_at": j.get("due_at").isoformat() if j.get("due_at") else None,
        "created_at": j.get("created_at").isoformat() if j.get("created_at") else None,
    }
