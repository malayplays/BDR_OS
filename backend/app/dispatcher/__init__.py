# dispatcher/ — deterministic core (Rule 5 + priority_score).
# LLM only for morning-plan narrative polish (behind flag, default off).

from app.dispatcher.autoapprove import (
    AutoApproveViolation,
    is_auto_approved,
    load_whitelist,
    validate_whitelist,
)
from app.dispatcher.ev import compute_ev
from app.dispatcher.job_factory import create_job, estimated_minutes, jobs_from_events
from app.dispatcher.morning_plan import build_today_payload
from app.dispatcher.ranker import compute_priority_score, rank_jobs, urgency_boost

__all__ = [
    "compute_ev",
    "create_job",
    "estimated_minutes",
    "jobs_from_events",
    "rank_jobs",
    "compute_priority_score",
    "urgency_boost",
    "build_today_payload",
    "is_auto_approved",
    "load_whitelist",
    "validate_whitelist",
    "AutoApproveViolation",
]
