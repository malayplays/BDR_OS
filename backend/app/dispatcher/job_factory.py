"""Job factory — Plan + events + agent-chain requests → Job dicts.

Consumes Plan, events, agent-chain requests and creates Job records with:
  - funnel_stage (from JOB_TYPE_TO_STAGE)
  - expected_value (from live blended rates via ev.py formulas)
  - due_at (convert-stage: +4h on positive replies)
  - estimated_minutes (from effort.yaml)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import yaml

from app.dispatcher.ev import JOB_TYPE_TO_STAGE, compute_ev
from app.engine.types import Event, EventType, RateRow


def _load_effort() -> dict[str, int]:
    path = os.path.join(os.path.dirname(__file__), "effort.yaml")
    with open(path) as f:
        return yaml.safe_load(f) or {}


_EFFORT: dict[str, int] | None = None


def get_effort() -> dict[str, int]:
    global _EFFORT
    if _EFFORT is None:
        _EFFORT = _load_effort()
    return _EFFORT


def estimated_minutes(job_type: str) -> int:
    return get_effort().get(job_type, 10)


def create_job(
    *,
    job_type: str,
    agent: str | None = None,
    trigger: dict | None = None,
    account_ref: str | None = None,
    contact_ref: str | None = None,
    persona_tier: str | None = None,
    channel: str | None = None,
    rates: list[RateRow] | None = None,
    due_at: datetime | None = None,
    input_payload: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Create a job dict ready for DB insertion.

    Returns a plain dict (not an ORM object) to keep the dispatcher I/O-free
    at the logic layer. The caller persists.
    """
    _now = now or datetime.utcnow()
    stage = JOB_TYPE_TO_STAGE.get(job_type, "create")
    ev = compute_ev(job_type, persona_tier, channel, rates)
    est = estimated_minutes(job_type)

    return {
        "job_type": job_type,
        "funnel_stage": stage,
        "agent": agent or job_type,
        "trigger": trigger,
        "account_ref": account_ref,
        "contact_ref": contact_ref,
        "status": "pending",
        "expected_value": ev,
        "priority_score": 0.0,  # ranker fills this
        "estimated_minutes": est,
        "input_payload": input_payload,
        "due_at": due_at,
        "created_at": _now,
    }


def jobs_from_events(
    events: list[Event],
    *,
    rates: list[RateRow] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Scan events and emit chained jobs (e.g., positive_reply → book_response with +4h SLA)."""
    _now = now or datetime.utcnow()
    jobs: list[dict] = []

    for e in events:
        if e.event_type == EventType.POSITIVE_REPLY:
            due = e.occurred_at + timedelta(hours=4)
            jobs.append(create_job(
                job_type="book_response",
                agent="book_response",
                trigger={"kind": "event", "ref": e.event_type, "occurred_at": e.occurred_at.isoformat()},
                account_ref=e.account_ref,
                contact_ref=e.contact_ref,
                persona_tier=e.persona_tier,
                channel=e.channel,
                rates=rates,
                due_at=due,
                now=_now,
            ))

        elif e.event_type == EventType.MEETING_NO_SHOW:
            jobs.append(create_job(
                job_type="no_show_recovery",
                agent="no_show_recovery",
                trigger={"kind": "event", "ref": e.event_type, "occurred_at": e.occurred_at.isoformat()},
                account_ref=e.account_ref,
                contact_ref=e.contact_ref,
                persona_tier=e.persona_tier,
                rates=rates,
                now=_now,
            ))

        elif e.event_type == EventType.DORMANCY_REQUALIFIED:
            jobs.append(create_job(
                job_type="dormancy_requalify",
                agent="research_brief",
                trigger={"kind": "event", "ref": e.event_type, "occurred_at": e.occurred_at.isoformat()},
                account_ref=e.account_ref,
                contact_ref=e.contact_ref,
                persona_tier=e.persona_tier,
                rates=rates,
                now=_now,
            ))

    return jobs
