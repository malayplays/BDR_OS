"""Policy guardrails — single chokepoint for ALL outbound writes.

Every write-back must pass through `check()` before touching an adapter write method.
"""

import json
import os
from datetime import datetime

import yaml

from app.models.enums import VerdictResult
from app.schemas import Verdict


def _load_policy_yaml() -> dict:
    path = os.path.join(os.path.dirname(__file__), "policy.yaml")
    if os.path.exists(path):
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _strategic_accounts() -> list[str]:
    raw = os.getenv("STRATEGIC_ACCOUNTS", "[]")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _draft_only_until() -> datetime | None:
    raw = os.getenv("DRAFT_ONLY_UNTIL", "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _max_new_outbound_per_day() -> int:
    return int(os.getenv("MAX_NEW_OUTBOUND_PER_DAY", "40"))


class WriteBackAction:
    """Describes an intended write-back for policy evaluation."""

    def __init__(
        self,
        *,
        action_type: str,
        account_ref: str | None = None,
        contact_ref: str | None = None,
        channel: str | None = None,
        is_customer_facing: bool = False,
        daily_new_outbound_count: int = 0,
    ) -> None:
        self.action_type = action_type
        self.account_ref = account_ref
        self.contact_ref = contact_ref
        self.channel = channel
        self.is_customer_facing = is_customer_facing
        self.daily_new_outbound_count = daily_new_outbound_count


def check(action: WriteBackAction) -> Verdict:
    """Evaluate guardrails. Returns ALLOW, BLOCK, or REQUIRE_APPROVAL."""
    now = datetime.utcnow()

    # 1. Strategic account block — always blocks automated writes
    strategic = _strategic_accounts()
    if action.account_ref and action.account_ref in strategic:
        return Verdict(
            result=VerdictResult.BLOCK,
            reason="Strategic-tier account — automated writes blocked; manual-only.",
            policy_flag="strategic_account_block",
        )

    # 2. Daily new outbound rate limit
    max_outbound = _max_new_outbound_per_day()
    if action.daily_new_outbound_count >= max_outbound:
        return Verdict(
            result=VerdictResult.REQUIRE_APPROVAL,
            reason=f"Daily new outbound limit reached ({action.daily_new_outbound_count}/{max_outbound}).",
            policy_flag="rate_limit_exceeded",
        )

    # 3. Draft-only blanket rule during ramp period
    draft_until = _draft_only_until()
    if draft_until and now < draft_until and action.is_customer_facing:
        if action.action_type == "create_draft":
            return Verdict(
                result=VerdictResult.ALLOW,
                reason="Draft creation allowed during draft-only period.",
            )
        return Verdict(
            result=VerdictResult.BLOCK,
            reason=f"Customer-facing writes blocked until {draft_until.date()} (draft-only period).",
            policy_flag="draft_only_block",
        )

    # 4. Customer-facing writes always require approval (v1 — no auto-send lane)
    if action.is_customer_facing:
        return Verdict(
            result=VerdictResult.REQUIRE_APPROVAL,
            reason="Customer-facing action requires approval.",
        )

    return Verdict(result=VerdictResult.ALLOW)
