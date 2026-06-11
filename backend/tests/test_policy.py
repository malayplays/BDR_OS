"""Policy guardrail tests:
- test_policy_draft_only — customer-facing write w/o approval verdict → BLOCK
- test_policy_strategic_block — write targeting strategic-tier account → BLOCK even with approval
- test_policy_rate_limit — 41st new outbound of the day → REQUIRE_APPROVAL with policy_flag
"""

import pytest

from app.models.enums import VerdictResult
from app.policy.guardrails import WriteBackAction, check


@pytest.fixture(autouse=True)
def _policy_env(monkeypatch):
    monkeypatch.setenv("DRAFT_ONLY_UNTIL", "2099-12-31")
    monkeypatch.setenv("MAX_NEW_OUTBOUND_PER_DAY", "40")
    monkeypatch.setenv("STRATEGIC_ACCOUNTS", '["acct-001", "acct-002", "acct-003"]')


def test_policy_draft_only():
    """Customer-facing write (non-draft) during draft-only period → BLOCK."""
    action = WriteBackAction(
        action_type="send",
        account_ref="acct-010",
        is_customer_facing=True,
    )
    verdict = check(action)
    assert verdict.result == VerdictResult.BLOCK
    assert "draft-only" in verdict.reason.lower() or "draft" in verdict.policy_flag.lower()


def test_policy_draft_only_allows_draft():
    """Creating a draft is allowed even during draft-only period."""
    action = WriteBackAction(
        action_type="create_draft",
        account_ref="acct-010",
        is_customer_facing=True,
    )
    verdict = check(action)
    assert verdict.result == VerdictResult.ALLOW


def test_policy_strategic_block():
    """Write targeting strategic-tier account → BLOCK even with approval."""
    action = WriteBackAction(
        action_type="create_draft",
        account_ref="acct-001",  # strategic
        is_customer_facing=True,
    )
    verdict = check(action)
    assert verdict.result == VerdictResult.BLOCK
    assert "strategic" in verdict.reason.lower()


def test_policy_strategic_block_non_customer_facing():
    """Even non-customer-facing writes to strategic accounts are blocked."""
    action = WriteBackAction(
        action_type="log_activity",
        account_ref="acct-002",  # strategic
        is_customer_facing=False,
    )
    verdict = check(action)
    assert verdict.result == VerdictResult.BLOCK


def test_policy_rate_limit():
    """41st new outbound of the day → REQUIRE_APPROVAL with policy_flag."""
    action = WriteBackAction(
        action_type="create_draft",
        account_ref="acct-010",
        is_customer_facing=True,
        daily_new_outbound_count=41,
    )
    verdict = check(action)
    assert verdict.result == VerdictResult.REQUIRE_APPROVAL
    assert verdict.policy_flag == "rate_limit_exceeded"


def test_policy_rate_limit_at_boundary():
    """Exactly at limit (40) → REQUIRE_APPROVAL."""
    action = WriteBackAction(
        action_type="create_draft",
        account_ref="acct-010",
        is_customer_facing=True,
        daily_new_outbound_count=40,
    )
    verdict = check(action)
    assert verdict.result == VerdictResult.REQUIRE_APPROVAL


def test_policy_allows_internal_write():
    """Non-customer-facing write to non-strategic account → ALLOW."""
    action = WriteBackAction(
        action_type="log_activity",
        account_ref="acct-010",
        is_customer_facing=False,
    )
    verdict = check(action)
    assert verdict.result == VerdictResult.ALLOW
