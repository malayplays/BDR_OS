"""Scenario: guardrail_gauntlet — strategic account in every flow → zero automated
touches; rate-limit day (41+ outbound) → flagged; DRAFT_ONLY end-to-end → zero
`send` calls anywhere (spy on EmailAdapter).

Includes meta-test: grep production code for any adapter write call not threading
a Verdict — must be zero.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from app.models.enums import VerdictResult
from app.models.job import Job
from app.policy.guardrails import WriteBackAction
from tests.e2e.sim import SimulationHarness


@pytest.fixture()
def harness():
    return SimulationHarness(
        start_date=datetime(2026, 6, 9, 7, 0),
        strategic_accounts=["acct-001", "acct-002", "acct-003"],
        draft_only_until="2099-12-31",
        max_outbound_per_day=40,
    )


class TestStrategicAccountBlock:
    """Strategic accounts: zero automated touches in every flow."""

    def test_strategic_account_blocks_all_customer_facing(self, harness: SimulationHarness):
        """Every customer-facing write to a strategic account is BLOCKED."""
        db = harness.db()

        for account_ref in ["acct-001", "acct-002", "acct-003"]:
            for job_type in ["outreach_draft", "book_response", "reconfirm",
                             "reminder_24h", "no_show_recovery"]:
                job = harness.create_job(
                    db,
                    job_type=job_type,
                    agent="copy",
                    funnel_stage="create",
                    account_ref=account_ref,
                    is_customer_facing=True,
                )
                harness.run_agent(db, job)
                v = harness.approve_job(db, job)

                assert v is not None
                assert v.result == VerdictResult.BLOCK, (
                    f"Strategic account {account_ref} should BLOCK {job_type}, "
                    f"got {v.result}"
                )
                assert "strategic" in v.reason.lower() or "Strategic" in v.reason

    def test_strategic_account_blocks_even_internal(self, harness: SimulationHarness):
        """Strategic accounts block ALL automated writes — including internal artifacts.

        Per guardrails.py: strategic-account check runs before any other rule.
        The agent may *prepare* materials (the brief gets generated), but the
        write-back is blocked.  This matches AGENTS.md: "agents may prepare
        materials but write-back is blocked; job converts to manual-only."
        """
        db = harness.db()

        brief_job = harness.create_job(
            db,
            job_type="research_brief",
            agent="research_brief",
            funnel_stage="create",
            account_ref="acct-001",  # strategic
            is_customer_facing=False,
        )
        harness.run_agent(db, brief_job)
        v = harness.approve_job(db, brief_job)

        # Strategic account blocks ALL automated writes
        assert v.result == VerdictResult.BLOCK
        assert "strategic" in (v.reason or "").lower()

    def test_zero_automated_touches_strategic(self, harness: SimulationHarness):
        """No write-back refs should exist for strategic account customer-facing jobs."""
        db = harness.db()
        account_ref = "acct-001"

        for jtype in ["outreach_draft", "book_response", "reconfirm"]:
            job = harness.create_job(
                db, job_type=jtype, agent="copy",
                account_ref=account_ref, is_customer_facing=True,
            )
            harness.run_agent(db, job)
            harness.approve_job(db, job)

        # No write-back refs for strategic accounts
        strategic_wb = db.query(Job).filter(
            Job.account_ref == account_ref,
            Job.write_back_ref.isnot(None),
            Job.job_type.in_(["outreach_draft", "book_response", "reconfirm"]),
        ).all()
        assert len(strategic_wb) == 0, "Strategic accounts must have zero write-backs"


class TestRateLimitDay:
    """41+ outbound in a day → flagged with REQUIRE_APPROVAL."""

    def test_rate_limit_flags_at_41(self, harness: SimulationHarness):
        account_ref = "acct-010"  # non-strategic

        # Simulate 40 outbound touches (at the limit)
        for i in range(40):
            action = WriteBackAction(
                action_type="create_draft",
                account_ref=account_ref,
                is_customer_facing=True,
                daily_new_outbound_count=i,
            )
            v = harness.check_policy(action)
            # Under 40, draft-only allows create_draft
            assert v.result in {VerdictResult.ALLOW, VerdictResult.REQUIRE_APPROVAL}

        # The 41st should be flagged
        action_41 = WriteBackAction(
            action_type="create_draft",
            account_ref=account_ref,
            is_customer_facing=True,
            daily_new_outbound_count=40,  # 0-indexed, this is the 41st
        )
        v41 = harness.check_policy(action_41)
        assert v41.result == VerdictResult.REQUIRE_APPROVAL
        assert "rate_limit" in (v41.policy_flag or "").lower() or "limit" in (v41.reason or "").lower()


class TestDraftOnlyEndToEnd:
    """During DRAFT_ONLY period, zero send() calls anywhere."""

    def test_zero_send_calls_in_draft_only(self, harness: SimulationHarness):
        """Spy on EmailAdapter — no send() calls during draft-only period."""
        db = harness.db()

        # Run multiple customer-facing jobs through the pipeline
        for i in range(5):
            job = harness.create_job(
                db,
                job_type="outreach_draft",
                agent="copy",
                funnel_stage="create",
                account_ref=f"acct-{10+i:03d}",
                is_customer_facing=True,
            )
            harness.run_agent(db, job)
            harness.approve_job(db, job)

        # Check: zero send() calls on the email adapter
        assert len(harness.email_adapter.sent) == 0, (
            f"Expected zero send() calls during DRAFT_ONLY, got {len(harness.email_adapter.sent)}"
        )

    def test_send_action_blocked_during_draft_only(self, harness: SimulationHarness):
        """Explicitly: send action type is BLOCKED during draft-only."""
        action = WriteBackAction(
            action_type="send",
            account_ref="acct-010",
            is_customer_facing=True,
            daily_new_outbound_count=0,
        )
        v = harness.check_policy(action)
        assert v.result == VerdictResult.BLOCK
        assert "draft" in (v.reason or "").lower() or "draft_only" in (v.policy_flag or "")


class TestMetaVerdictThreading:
    """Meta-test: grep production code for any adapter write call not threading
    a Verdict — must be zero.

    Every adapter write method signature must accept a Verdict parameter.
    """

    def test_all_adapter_writes_thread_verdict(self):
        """Every write method on adapter interfaces accepts a Verdict as first arg."""
        adapters_dir = Path(__file__).resolve().parent.parent.parent / "app" / "adapters"
        interfaces_dir = adapters_dir / "interfaces"

        write_methods_without_verdict = []

        for py_file in interfaces_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            source = py_file.read_text()
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                # Heuristic: write methods are non-read, non-dunder, non-private
                if node.name.startswith("_") or node.name.startswith("get_") or node.name.startswith("list_"):
                    continue
                if node.name in {"detect_autoreply", "watch_replies", "find_slots", "search_accounts"}:
                    continue
                # Check for known read methods
                if node.name.startswith("pull_") or node.name.startswith("enrich_"):
                    continue

                # This should be a write method — check it accepts Verdict
                arg_names = [a.arg for a in node.args.args]
                arg_annotations = []
                for a in node.args.args:
                    if a.annotation:
                        if isinstance(a.annotation, ast.Name):
                            arg_annotations.append(a.annotation.id)
                        elif isinstance(a.annotation, ast.Attribute):
                            arg_annotations.append(a.annotation.attr)

                if "Verdict" not in arg_annotations and "v" not in arg_names[:3]:
                    # Skip 'self'
                    if len(arg_names) > 1:
                        write_methods_without_verdict.append(
                            f"{py_file.name}::{node.name}(args={arg_names})"
                        )

        assert len(write_methods_without_verdict) == 0, (
            "Adapter write methods missing Verdict parameter:\n"
            + "\n".join(write_methods_without_verdict)
        )

    def test_mock_adapter_writes_thread_verdict(self):
        """Every mock adapter write method also accepts Verdict."""
        mock_dir = Path(__file__).resolve().parent.parent.parent / "app" / "adapters" / "mock"

        write_methods_without_verdict = []

        for py_file in mock_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            source = py_file.read_text()
            tree = ast.parse(source)

            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                # Write methods: create_, update_, log_, send
                if not any(node.name.startswith(p) for p in ["create_", "update_", "log_", "send"]):
                    continue

                arg_names = [a.arg for a in node.args.args]
                arg_annotations = []
                for a in node.args.args:
                    if a.annotation and isinstance(a.annotation, ast.Name):
                        arg_annotations.append(a.annotation.id)

                if "Verdict" not in arg_annotations and "v" not in arg_names[:3]:
                    write_methods_without_verdict.append(
                        f"{py_file.name}::{node.name}(args={arg_names})"
                    )

        assert len(write_methods_without_verdict) == 0, (
            "Mock adapter write methods missing Verdict parameter:\n"
            + "\n".join(write_methods_without_verdict)
        )
