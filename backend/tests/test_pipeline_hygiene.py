"""Tests for Pipeline Hygiene Agent — AGENTS.md §9.

Merge bar (all must pass):
- test_each_detection_rule — one fixture scenario per rule, fires exactly once,
  no false positive on clean data.
- test_autofix_whitelist_only — sweep on fixtures: auto-fixed items ⊆ whitelist;
  everything customer-adjacent is `pending` jobs.
- test_strategic_manual_only — stale strategic-account thread → manual task, zero
  draft jobs.
- test_idempotent — running sweep twice produces no duplicate jobs (dedupe on
  finding fingerprint).
- test_show_rate_risk_flag — Golden 1: unaccepted-invite meeting 3 days out →
  reconfirm proposal flagged "show-rate risk".
"""

from datetime import date, datetime, timedelta

from app.adapters.interfaces.types import (
    Attendee,
    CalEvent,
    CRMTask,
    ThreadSummary,
)
from app.agents.pipeline_hygiene import (
    AUTOFIX_ACTIONS,
    ContactTouchRecord,
    NoShowRecord,
    detect_no_show_without_recovery,
    detect_overdue_tasks,
    detect_stale_sequence,
    detect_unaccepted_invite,
    detect_unreplied_thread,
    run_sweep,
)

NOW = datetime(2026, 6, 11, 2, 30)


# ── Fixtures ──────────────────────────────────────────────────────────

def _stale_thread() -> ThreadSummary:
    """Thread unreplied for 7 days."""
    return ThreadSummary(
        ref="acct-100-thread-1",
        subject="Follow-up on Devin demo",
        last_message_at=NOW - timedelta(days=7),
        snippet="Looking forward to your thoughts…",
    )


def _fresh_thread() -> ThreadSummary:
    """Thread replied 2 days ago — should NOT fire."""
    return ThreadSummary(
        ref="acct-200-thread-2",
        subject="Quick question about pricing",
        last_message_at=NOW - timedelta(days=2),
        snippet="Thanks for the info!",
    )


def _unaccepted_meeting(days_out: int = 3) -> CalEvent:
    """Meeting with unaccepted invite."""
    return CalEvent(
        ref="acct-100-meeting-1",
        title="Devin deep-dive",
        start=NOW + timedelta(days=days_out),
        end=NOW + timedelta(days=days_out, hours=1),
        attendees=[
            Attendee(email="prospect@example.com", response_status="needsAction"),
            Attendee(email="malay@cognition.dev", response_status="accepted"),
        ],
    )


def _accepted_meeting() -> CalEvent:
    """Meeting with all accepted — should NOT fire."""
    return CalEvent(
        ref="acct-200-meeting-2",
        title="Weekly sync",
        start=NOW + timedelta(days=5),
        end=NOW + timedelta(days=5, hours=1),
        attendees=[
            Attendee(email="contact@bigco.com", response_status="accepted"),
            Attendee(email="malay@cognition.dev", response_status="accepted"),
        ],
    )


def _stale_contact() -> ContactTouchRecord:
    """Contact with 4 touches, no reply in 25 days."""
    return ContactTouchRecord(
        contact_ref="contact-300",
        account_ref="acct-300",
        touch_count=4,
        last_reply_at=None,
        first_touch_at=NOW - timedelta(days=25),
        in_active_sequence=True,
    )


def _healthy_contact() -> ContactTouchRecord:
    """Contact with 2 touches — below threshold, should NOT fire."""
    return ContactTouchRecord(
        contact_ref="contact-400",
        account_ref="acct-400",
        touch_count=2,
        last_reply_at=NOW - timedelta(days=5),
        first_touch_at=NOW - timedelta(days=10),
    )


def _no_show_without_recovery() -> NoShowRecord:
    return NoShowRecord(
        event_ref="evt-no-show-1",
        account_ref="acct-500",
        contact_ref="contact-500",
        occurred_at=NOW - timedelta(days=2),
        has_recovery_sequence=False,
    )


def _no_show_with_recovery() -> NoShowRecord:
    """No-show that already has recovery — should NOT fire."""
    return NoShowRecord(
        event_ref="evt-no-show-2",
        account_ref="acct-600",
        contact_ref="contact-600",
        occurred_at=NOW - timedelta(days=1),
        has_recovery_sequence=True,
    )


def _overdue_bdr_task() -> CRMTask:
    """BDR-OS-created task overdue by 5 days → auto-fixable."""
    return CRMTask(
        ref="bdr-os-task-001",
        account_ref="acct-700",
        subject="[BDR-OS] Send follow-up",
        due_date=date(2026, 6, 3),
        status="open",
    )


def _overdue_external_task() -> CRMTask:
    """Non-BDR-OS task overdue by 5 days → proposed job, not auto-fix."""
    return CRMTask(
        ref="crm-task-ext-001",
        account_ref="acct-800",
        subject="Schedule QBR",
        due_date=date(2026, 6, 3),
        status="open",
    )


def _current_task() -> CRMTask:
    """Task not yet due — should NOT fire."""
    return CRMTask(
        ref="crm-task-current",
        account_ref="acct-900",
        subject="Prepare deck",
        due_date=date(2026, 6, 15),
        status="open",
    )


# ── Test 1: test_each_detection_rule ──────────────────────────────────


class TestEachDetectionRule:
    """One fixture scenario per rule, fires exactly once, no false positive on
    clean data."""

    def test_unreplied_thread_fires(self):
        findings = detect_unreplied_thread([_stale_thread()], NOW)
        assert len(findings) == 1
        assert findings[0].rule == "unreplied_thread"
        assert findings[0].evidence["days_waiting"] == 7

    def test_unreplied_thread_no_false_positive(self):
        findings = detect_unreplied_thread([_fresh_thread()], NOW)
        assert len(findings) == 0

    def test_unaccepted_invite_fires(self):
        findings = detect_unaccepted_invite([_unaccepted_meeting()], NOW)
        assert len(findings) == 1
        assert findings[0].rule == "unaccepted_invite"

    def test_unaccepted_invite_no_false_positive(self):
        findings = detect_unaccepted_invite([_accepted_meeting()], NOW)
        assert len(findings) == 0

    def test_stale_sequence_fires(self):
        findings = detect_stale_sequence([_stale_contact()], NOW)
        assert len(findings) == 1
        assert findings[0].rule == "stale_sequence"

    def test_stale_sequence_no_false_positive(self):
        findings = detect_stale_sequence([_healthy_contact()], NOW)
        assert len(findings) == 0

    def test_no_show_no_recovery_fires(self):
        findings = detect_no_show_without_recovery([_no_show_without_recovery()])
        assert len(findings) == 1
        assert findings[0].rule == "no_show_no_recovery"

    def test_no_show_no_recovery_no_false_positive(self):
        findings = detect_no_show_without_recovery([_no_show_with_recovery()])
        assert len(findings) == 0

    def test_overdue_task_fires(self):
        findings = detect_overdue_tasks([_overdue_bdr_task()], NOW)
        assert len(findings) == 1
        assert findings[0].rule == "overdue_task"

    def test_overdue_task_no_false_positive(self):
        findings = detect_overdue_tasks([_current_task()], NOW)
        assert len(findings) == 0


# ── Test 2: test_autofix_whitelist_only ───────────────────────────────


def test_autofix_whitelist_only():
    """Sweep on fixtures: auto-fixed items ⊆ whitelist; everything
    customer-adjacent is `pending` proposed jobs."""
    report = run_sweep(
        now=NOW,
        threads=[_stale_thread()],
        meetings=[_unaccepted_meeting()],
        contacts=[_stale_contact()],
        no_shows=[_no_show_without_recovery()],
        tasks=[_overdue_bdr_task(), _overdue_external_task()],
    )

    # All auto-fixed actions must be in the whitelist
    for fix in report.auto_fixed:
        assert fix.action in AUTOFIX_ACTIONS, (
            f"Auto-fix action '{fix.action}' is not in the whitelist"
        )

    # Customer-adjacent findings must appear as proposed jobs, not auto-fixed
    proposed_rules = {
        p["proposed_job"]["job_type"] for p in report.proposed
    }
    # unreplied thread → follow_up (customer-adjacent)
    assert "follow_up" in proposed_rules
    # unaccepted invite → reconfirm (customer-adjacent)
    assert "reconfirm" in proposed_rules
    # no-show → no_show_recovery (customer-adjacent)
    assert "no_show_recovery" in proposed_rules

    # The BDR-OS task should be auto-fixed (close_stale_bdr_task)
    autofix_actions = {fix.action for fix in report.auto_fixed}
    assert "close_stale_bdr_task" in autofix_actions

    # External overdue task → proposed job, not auto-fixed
    ext_task_proposed = [
        p for p in report.proposed
        if p["proposed_job"]["job_type"] == "task_cleanup"
    ]
    assert len(ext_task_proposed) == 1


# ── Test 3: test_strategic_manual_only ────────────────────────────────


def test_strategic_manual_only():
    """Stale strategic-account thread → manual task, zero draft jobs."""
    strategic_thread = ThreadSummary(
        ref="acct-strat-thread-1",
        subject="Partnership discussion",
        last_message_at=NOW - timedelta(days=10),
        snippet="Let me get back to you…",
    )
    strategic_accounts = {"acct-strat"}

    report = run_sweep(
        now=NOW,
        threads=[strategic_thread],
        meetings=[],
        contacts=[],
        no_shows=[],
        tasks=[],
        strategic_accounts=strategic_accounts,
        thread_account_map={"acct-strat-thread-1": "acct-strat"},
    )

    # Should have proposed jobs, not auto-fixed
    assert len(report.proposed) == 1
    proposed = report.proposed[0]
    assert proposed["proposed_job"]["manual_only"] is True

    # Zero draft jobs (nothing in proposed should lack manual_only for strategic)
    draft_jobs = [
        p for p in report.proposed
        if not p["proposed_job"]["manual_only"]
    ]
    assert len(draft_jobs) == 0


# ── Test 4: test_idempotent ───────────────────────────────────────────


def test_idempotent():
    """Running sweep twice produces no duplicate jobs (dedupe on fingerprint)."""
    sweep_kwargs = dict(
        now=NOW,
        threads=[_stale_thread()],
        meetings=[_unaccepted_meeting()],
        contacts=[_stale_contact()],
        no_shows=[_no_show_without_recovery()],
        tasks=[_overdue_bdr_task(), _overdue_external_task()],
    )

    # First sweep
    report1 = run_sweep(**sweep_kwargs)
    assert len(report1.proposed) > 0

    # Second sweep with first sweep's ALL fingerprints (proposed + auto-fixed)
    report2 = run_sweep(**sweep_kwargs, existing_fingerprints=report1.all_fingerprints)

    # No new proposed jobs (all fingerprints already exist)
    assert len(report2.proposed) == 0
    assert report2.findings_count == 0


# ── Test 5: test_show_rate_risk_flag ──────────────────────────────────


def test_show_rate_risk_flag():
    """Golden 1: unaccepted-invite meeting 3 days out → reconfirm proposal
    flagged 'show-rate risk'."""
    meeting_3d = _unaccepted_meeting(days_out=3)
    report = run_sweep(
        now=NOW,
        threads=[],
        meetings=[meeting_3d],
        contacts=[],
        no_shows=[],
        tasks=[],
    )

    assert len(report.proposed) == 1
    proposed = report.proposed[0]
    assert proposed["proposed_job"]["job_type"] == "reconfirm"
    assert "show-rate risk" in proposed["proposed_job"]["flags"]
    assert "show-rate risk" in proposed["evidence"]["flags"]
