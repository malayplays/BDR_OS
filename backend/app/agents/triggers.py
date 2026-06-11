"""Trigger wiring — signal ≥0.5 from EnrichmentAdapter poll → job created.

Called by the scheduler or manually to poll for new enrichment signals
and create research_brief jobs for qualifying signals.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.adapters.interfaces.types import Signal


def signal_qualifies(signal: Signal) -> bool:
    """Signal must have strength ≥ 0.5 to trigger a research_brief job."""
    return signal.strength >= 0.5


def build_research_brief_job_from_signal(signal: Signal, account_ref: str) -> dict:
    """Build a job creation payload from a qualifying signal."""
    return {
        "job_type": "research_brief",
        "funnel_stage": "create",
        "agent": "research_brief",
        "account_ref": account_ref,
        "trigger": {
            "type": "enrichment_signal",
            "signal_kind": signal.kind,
            "signal_strength": signal.strength,
            "detected_at": signal.detected_at.isoformat(),
        },
        "input_payload": {
            "signal": signal.model_dump(mode="json"),
        },
    }


async def poll_signals_and_create_jobs(
    enrichment_adapter: Any,
    crm_adapter: Any,
    since: datetime,
    create_job_fn: Any,
) -> list[dict]:
    """Poll EnrichmentAdapter for signals ≥0.5, create research_brief jobs.

    Returns list of created job dicts.
    """
    signals = await enrichment_adapter.get_signals(since)
    created_jobs: list[dict] = []

    for signal in signals:
        if not signal_qualifies(signal):
            continue

        # Resolve account_ref from domain
        accounts = await crm_adapter.search_accounts(
            type("Q", (), {"tier": None, "owner": None, "last_touched_before": None, "status": None})()  # noqa: E501
        )
        # Find matching account by domain
        account_ref = None
        for acct in accounts:
            if acct.domain == signal.account_domain:
                account_ref = acct.ref
                break

        if account_ref is None:
            # No matching account in CRM — skip
            continue

        job_payload = build_research_brief_job_from_signal(signal, account_ref)
        create_job_fn(job_payload)
        created_jobs.append(job_payload)

    return created_jobs
