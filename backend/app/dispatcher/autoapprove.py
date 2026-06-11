"""Auto-approve lane — whitelist enforcement with DRAFT_ONLY safety.

Job types listed in policy.yaml `auto_approve_whitelist` bypass the approval
queue. Safety invariant: customer_facing-tagged types are NEVER auto-approved
while `now < DRAFT_ONLY_UNTIL`.

Config-load-time assertion: if a customer_facing type appears in the whitelist
while DRAFT_ONLY is active, the app refuses to boot (raises on import / load).
"""

from __future__ import annotations

import os
from datetime import datetime

import yaml

# Job types tagged as customer_facing — these MUST NOT be auto-approved during DRAFT_ONLY.
CUSTOMER_FACING_TYPES: frozenset[str] = frozenset({
    "outreach_draft",
    "book_response",
    "confirmation_24h",
    "confirmation_am",
    "reconfirm",
    "pull_in_offer",
    "reschedule",
    "no_show_recovery",
    "reporting_manager_update",
})


class AutoApproveViolation(Exception):
    """Raised at config-load time if a customer_facing type is whitelisted while DRAFT_ONLY is active."""
    pass


def _load_policy_yaml() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "policy", "policy.yaml")
    path = os.path.normpath(path)
    if os.path.exists(path):
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_draft_only_until() -> datetime | None:
    raw = os.getenv("DRAFT_ONLY_UNTIL", "")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    policy = _load_policy_yaml()
    draft_cfg = policy.get("draft_only", {})
    if draft_cfg.get("enabled") and draft_cfg.get("until"):
        try:
            return datetime.fromisoformat(draft_cfg["until"])
        except ValueError:
            pass
    return None


def load_whitelist() -> list[str]:
    """Load the auto-approve whitelist from policy.yaml."""
    policy = _load_policy_yaml()
    return policy.get("auto_approve_whitelist", [])


def validate_whitelist(
    whitelist: list[str] | None = None,
    *,
    now: datetime | None = None,
) -> None:
    """Config-load-time assertion: refuse to boot if customer_facing types
    appear in the auto-approve whitelist while DRAFT_ONLY is active.

    Raises AutoApproveViolation with a clear error message.
    """
    _now = now or datetime.utcnow()
    wl = whitelist if whitelist is not None else load_whitelist()
    draft_until = _get_draft_only_until()

    if draft_until is None or _now >= draft_until:
        return  # DRAFT_ONLY not active, no restriction

    violations = [jt for jt in wl if jt in CUSTOMER_FACING_TYPES]
    if violations:
        raise AutoApproveViolation(
            f"Auto-approve whitelist contains customer_facing types {violations} "
            f"but DRAFT_ONLY is active until {draft_until.date()}. "
            f"Remove these types from auto_approve_whitelist in policy.yaml or "
            f"disable DRAFT_ONLY first."
        )


def is_auto_approved(job_type: str, whitelist: list[str] | None = None) -> bool:
    """Check if a job type is auto-approved (on the whitelist)."""
    wl = whitelist if whitelist is not None else load_whitelist()
    return job_type in wl
