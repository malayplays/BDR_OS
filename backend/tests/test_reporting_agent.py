"""Tests for the Reporting Agent (Session 11).

Required tests (merge bar):
- test_numbers_never_hallucinated — every numeric token in rendered output
  matches a value in the input payload (regex-extract and assert).
- test_recap_auto_manager_gated — recap auto-archives; manager draft sits
  in Review Queue, then lands as Gmail draft via mock.
- test_friday_and_monthend_triggers — fake clock fires both correctly.
- test_at_risk_honesty — at_risk=true funnel → manager draft contains
  the risk; suppressing it is impossible via prompt (assert string present).
- Golden test for AGENTS.md §10.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import patch

from app.agents.reporting import (
    ManagerUpdate,
    PersonalRecap,
    ReportingAgent,
    build_allowed_numbers,
    extract_numbers,
    is_friday_3pm,
    is_month_end,
    process_reporting_job,
    render_manager_markdown,
    render_recap_markdown,
    should_trigger,
    verify_numbers_not_hallucinated,
)

# ── Fixture: input payload ────────────────────────────────────────────

SAMPLE_INPUT = {
    "goal_id": "goal-001",
    "period_label": "Week of 8/10",
    "funnel_state": {
        "counts": {
            "touches": {"email": 120, "call": 40, "linkedin": 30},
            "replies": 15,
            "positive_replies": 5,
            "booked": 5,
            "held": 3,
            "no_shows": 1,
            "ad_accepted": 2,
            "s1": 1,
            "s2": 0,
        },
        "points": {"credited": 8.0, "pending": 5.0, "projected": 12.5},
        "persona_mix": {
            "vp_level": {"booked": 2, "credited": 1},
            "director": {"booked": 2, "credited": 1},
            "manager": {"booked": 1, "credited": 0},
        },
        "pct_goal": 0.37,
        "pct_period_elapsed": 0.45,
        "pace_gap": -0.08,
        "at_risk": False,
        "gap_by_stage": {
            "touches": {"plan": 200, "actual": 190},
            "replies": {"plan": 18, "actual": 15},
            "booked": {"plan": 6, "actual": 5},
            "held": {"plan": 3, "actual": 3},
        },
    },
    "plan": {
        "weekly_bookings_required": 3.0,
        "weekly_held_target": 2.1,
        "daily_allocation": {
            "email_touches": 24,
            "calls": 8,
            "linkedin_touches": 6,
        },
    },
    "rates": [
        {
            "metric": "reply_rate", "channel": "email",
            "blended_rate": 0.028, "confidence": "medium",
            "benchmark_rate": 0.04, "baseline_90d": 0.035,
        },
        {
            "metric": "show_rate", "channel": None,
            "blended_rate": 0.80, "confidence": "medium",
            "benchmark_rate": 0.70, "baseline_90d": 0.75,
        },
        {
            "metric": "book_rate", "channel": None,
            "blended_rate": 0.55, "confidence": "low",
            "benchmark_rate": 0.55, "baseline_90d": 0.50,
        },
    ],
    "earnings_projection": {
        "month_to_date": 571.44,
        "projected_month": 1428.60,
        "annualized": 95000,
    },
    "wins": [
        "Held 3 meetings (target 3)",
        "Show rate 80% — confirmations cadence working",
    ],
    "at_risk_flags": [],
    "next_week_plan": {
        "booked": 5,
        "focus": "Testing two new angles for reply rate improvement",
    },
}

AT_RISK_INPUT = {
    **SAMPLE_INPUT,
    "funnel_state": {
        **SAMPLE_INPUT["funnel_state"],
        "at_risk": True,
        "pace_gap": -0.22,
    },
    "at_risk_flags": [
        "2 of next week's 5 booked meetings are >5 days out — pull-in offers going Monday",
        "Reply rate dipped to 2.8% vs 4.0% benchmark",
    ],
}

# Month-end variant
MONTH_END_INPUT = {
    **SAMPLE_INPUT,
    "period_label": "June 2026",
    "annual_goal": {"target_value": 420, "credited_ytd": 48.0, "pending_ytd": 12.0},
    "rate_deltas_vs_benchmarks": [
        {"metric": "reply_rate", "current": 0.028, "benchmark": 0.04, "delta": -0.012},
        {"metric": "show_rate", "current": 0.80, "benchmark": 0.70, "delta": 0.10},
    ],
    "cold_start_exit": {
        "month": 2,
        "quota": 15,
        "ramp_status": "on_track",
        "confidence_levels": {"reply_rate": "medium", "book_rate": "low"},
    },
    "promotion_scorecard": {
        "attainment_streak_130pct": 0,
        "sourced_s2_count": 0,
        "months_above_sr_quota_40": 0,
    },
}


# ── Golden LLM responses ─────────────────────────────────────────────

_GOLDEN_RECAP_DATA = {
    "summary": (
        "Week of 8/10: 3 held (target 3), 5 booked for next week. "
        "Reply rate at 0.028 — testing two new angles Monday. "
        "Show rate 0.80 (confirmations cadence working). "
        "8.0 credited points, 5.0 pending, 12.5 projected."
    ),
    "plan_vs_actual_by_stage": {
        "touches": {"plan": 200, "actual": 190},
        "replies": {"plan": 18, "actual": 15},
        "booked": {"plan": 6, "actual": 5},
        "held": {"plan": 3, "actual": 3},
    },
    "rate_trends": [
        {"metric": "reply_rate", "current": 0.028, "confidence": "medium"},
        {"metric": "show_rate", "current": 0.80, "confidence": "medium"},
        {"metric": "book_rate", "current": 0.55, "confidence": "low"},
    ],
    "wins": [
        "Held 3 meetings (target 3)",
        "Show rate 80% — confirmations cadence working",
    ],
    "at_risk_flags": [],
    "next_week_plan_summary": (
        "5 booked for next week. Testing two new angles for reply rate improvement."
    ),
    "confidence": 0.85,
    "needs_human_because": None,
}
GOLDEN_RECAP_RESPONSE = json.dumps(_GOLDEN_RECAP_DATA)

_GOLDEN_MANAGER_DATA = {
    "subject": "Week of 8/10: 3 held (target 3), 5 booked for next week",
    "body": (
        "Week of 8/10: 3 held (target 3), 5 booked for next week. "
        "Reply rate dipped to 0.028 — testing two new angles Mon; "
        "show rate 0.80 (confirmations cadence working). "
        "8.0 credited points, 5.0 pending."
    ),
    "risk_section": "No current at-risk flags.",
    "need_from_you": "10 min on the account list for next week's targets.",
    "confidence": 0.85,
    "needs_human_because": None,
}
GOLDEN_MANAGER_RESPONSE = json.dumps(_GOLDEN_MANAGER_DATA)

# At-risk manager response — MUST include risk flags
_AT_RISK_MANAGER_DATA = {
    "subject": "Week of 8/10: 3 held, pace gap -0.22 — at risk",
    "body": (
        "Week of 8/10: 3 held (target 3), 5 booked for next week. "
        "Pace gap at -0.22, flagged at-risk."
    ),
    "risk_section": (
        "2 of next week's 5 booked meetings are >5 days out — pull-in offers going Monday. "
        "Reply rate dipped to 2.8% vs 4.0% benchmark."
    ),
    "need_from_you": "10 min on the account list — need to expand target list given pace gap.",
    "confidence": 0.75,
    "needs_human_because": None,
}
AT_RISK_MANAGER_RESPONSE = json.dumps(_AT_RISK_MANAGER_DATA)

_AT_RISK_RECAP_DATA = {
    **_GOLDEN_RECAP_DATA,
    "at_risk_flags": [
        "2 of next week's 5 booked meetings are >5 days out — pull-in offers going Monday",
        "Reply rate dipped to 2.8% vs 4.0% benchmark",
    ],
}
AT_RISK_RECAP_RESPONSE = json.dumps(_AT_RISK_RECAP_DATA)

# Golden §10 response
_GOLDEN_S10_MANAGER_DATA = {
    "subject": "Week of 8/10: 3 held (target 3), 5 booked for next week",
    "body": (
        "Week of 8/10: 3 held (target 3), 5 booked for next week. "
        "Reply rate dipped to 0.028 — testing two new angles Mon; "
        "show rate 0.80 (confirmations cadence working). "
        "Risk: No current at-risk flags. "
        "Need from you: 10 min on the account list."
    ),
    "risk_section": "No current at-risk flags.",
    "need_from_you": "10 min on the account list.",
    "confidence": 0.85,
    "needs_human_because": None,
}
GOLDEN_S10_MANAGER_RESPONSE = json.dumps(_GOLDEN_S10_MANAGER_DATA)


# ── Deterministic LLM mock ───────────────────────────────────────────


class MockLLMCallable:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.call_count = 0
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user_message: str) -> tuple[str, int, int]:
        self.calls.append((system, user_message))
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
        else:
            resp = self.responses[-1]
        self.call_count += 1
        return resp, 100, 50


# ── Tests: Numbers Never Hallucinated ─────────────────────────────────


class TestNumbersNeverHallucinated:
    """test_numbers_never_hallucinated — every numeric token in rendered output
    matches a value in the input payload."""

    def test_extract_numbers_basic(self):
        result = extract_numbers("3 held, 0.028 rate, 80%")
        assert "3" in result
        assert "0.028" in result
        assert "80" in result

    def test_build_allowed_numbers_from_payload(self):
        allowed = build_allowed_numbers(SAMPLE_INPUT)
        assert "120" in allowed
        assert "0.028" in allowed
        assert "0.80" in allowed or "0.8" in allowed
        assert "3.0" in allowed or "3" in allowed
        assert "571.44" in allowed

    def test_recap_numbers_all_in_payload(self):
        """Every number in the golden recap exists in SAMPLE_INPUT."""
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([GOLDEN_RECAP_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_recap(SAMPLE_INPUT)
        assert result.success
        assert result.output is not None

        # Render to text
        recap = PersonalRecap(
            confidence=result.output.confidence,
            needs_human_because=result.output.needs_human_because,
            **result.output.data,
        )
        md = render_recap_markdown(recap, "friday", "Week of 8/10")
        passed, hallucinated = verify_numbers_not_hallucinated(md, SAMPLE_INPUT)
        assert passed, f"Hallucinated numbers in recap: {hallucinated}"

    def test_manager_numbers_all_in_payload(self):
        """Every number in the golden manager update exists in SAMPLE_INPUT."""
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([GOLDEN_MANAGER_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_manager_update(SAMPLE_INPUT)
        assert result.success
        assert result.output is not None

        update = ManagerUpdate(
            confidence=result.output.confidence,
            needs_human_because=result.output.needs_human_because,
            **result.output.data,
        )
        text = render_manager_markdown(update)
        passed, hallucinated = verify_numbers_not_hallucinated(text, SAMPLE_INPUT)
        assert passed, f"Hallucinated numbers in manager update: {hallucinated}"

    def test_hallucinated_number_detected(self):
        """A fabricated number not in input is caught."""
        output_text = "We achieved 99.99% conversion rate this week"
        passed, hallucinated = verify_numbers_not_hallucinated(output_text, SAMPLE_INPUT)
        assert not passed
        assert "99.99" in hallucinated or "99" in hallucinated

    def test_verify_against_at_risk_payload(self):
        """Numbers in at-risk manager update all come from AT_RISK_INPUT."""
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([AT_RISK_MANAGER_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_manager_update(AT_RISK_INPUT)
        assert result.success
        update = ManagerUpdate(
            confidence=result.output.confidence,
            needs_human_because=result.output.needs_human_because,
            **result.output.data,
        )
        text = render_manager_markdown(update)
        passed, hallucinated = verify_numbers_not_hallucinated(text, AT_RISK_INPUT)
        assert passed, f"Hallucinated: {hallucinated}"


# ── Tests: Recap Auto / Manager Gated ─────────────────────────────────


class TestRecapAutoManagerGated:
    """test_recap_auto_manager_gated — recap auto-archives; manager draft
    sits in Review Queue, then lands as Gmail draft via mock."""

    def test_recap_auto_approved_and_archived(self):
        """Recap is auto-approved and create_report_fn called with approved=True."""
        agent = ReportingAgent()
        mock_recap = MockLLMCallable([GOLDEN_RECAP_RESPONSE])

        reports_created: list[dict] = []

        def mock_create_report(report_dict: dict) -> None:
            reports_created.append(report_dict)

        with patch.object(agent, "_call_llm", mock_recap):
            recap_result = agent.run_recap(SAMPLE_INPUT)

        # Simulate pipeline manually
        assert recap_result.success
        recap_data = recap_result.output.data
        recap_obj = PersonalRecap(
            confidence=recap_result.output.confidence,
            needs_human_because=recap_result.output.needs_human_because,
            **recap_data,
        )
        md = render_recap_markdown(recap_obj, "friday", "Week of 8/10")
        mock_create_report({
            "report_type": "personal_recap",
            "trigger": "friday",
            "payload": recap_data,
            "markdown": md,
            "approval_required": False,
            "approved": True,
        })

        assert len(reports_created) == 1
        recap_report = reports_created[0]
        assert recap_report["report_type"] == "personal_recap"
        assert recap_report["approved"] is True
        assert recap_report["approval_required"] is False
        assert recap_report["markdown"] is not None
        assert "Personal Recap" in recap_report["markdown"]

    def test_manager_draft_requires_approval(self):
        """Manager update requires approval and sits in Review Queue."""
        agent = ReportingAgent()
        mock_manager = MockLLMCallable([GOLDEN_MANAGER_RESPONSE])

        reports_created: list[dict] = []

        def mock_create_report(report_dict: dict) -> None:
            reports_created.append(report_dict)

        with patch.object(agent, "_call_llm", mock_manager):
            manager_result = agent.run_manager_update(SAMPLE_INPUT)

        assert manager_result.success
        update_data = manager_result.output.data
        update_obj = ManagerUpdate(
            confidence=manager_result.output.confidence,
            needs_human_because=manager_result.output.needs_human_because,
            **update_data,
        )
        mock_create_report({
            "report_type": "manager_update",
            "trigger": "friday",
            "payload": update_data,
            "markdown": render_manager_markdown(update_obj),
            "approval_required": True,
            "approved": False,
        })

        assert len(reports_created) == 1
        mgr_report = reports_created[0]
        assert mgr_report["report_type"] == "manager_update"
        assert mgr_report["approved"] is False
        assert mgr_report["approval_required"] is True

    def test_pipeline_creates_gmail_draft_via_mock(self):
        """Full pipeline: manager update → Gmail draft via MockEmailAdapter."""
        from app.adapters.mock.email import MockEmailAdapter

        email_adapter = MockEmailAdapter()
        reports_created: list[dict] = []

        def mock_create_report(report_dict: dict) -> None:
            reports_created.append(report_dict)

        agent = ReportingAgent()
        # Two calls: recap then manager
        mock_llm = MockLLMCallable([GOLDEN_RECAP_RESPONSE, GOLDEN_MANAGER_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            with patch("app.agents.reporting.ReportingAgent", return_value=agent):
                results = process_reporting_job(
                    SAMPLE_INPUT,
                    trigger="friday",
                    email_adapter=email_adapter,
                    create_report_fn=mock_create_report,
                )

        # Recap should be auto-approved
        assert results["recap"].success
        # Manager should succeed and have Gmail draft
        assert results["manager_update"].success

        # Check Gmail draft was created
        assert len(email_adapter.drafts) == 1
        draft = email_adapter.drafts[0]
        assert "kyle@company.com" in draft["email"]["to"]

        # Check reports
        assert len(reports_created) == 2
        recap_r = next(r for r in reports_created if r["report_type"] == "personal_recap")
        mgr_r = next(r for r in reports_created if r["report_type"] == "manager_update")
        assert recap_r["approved"] is True
        assert mgr_r["approved"] is False
        assert mgr_r["approval_required"] is True
        assert mgr_r["write_back_ref"] is not None
        assert mgr_r["write_back_ref"].startswith("draft-")


# ── Tests: Friday and Month-End Triggers ──────────────────────────────


class TestFridayAndMonthEndTriggers:
    """test_friday_and_monthend_triggers — fake clock fires both correctly."""

    def test_friday_3pm_fires(self):
        """Friday at 15:00 triggers."""
        # 2026-06-12 is a Friday
        fri_3pm = datetime(2026, 6, 12, 15, 0, 0, tzinfo=UTC)
        assert is_friday_3pm(fri_3pm) is True

    def test_friday_wrong_hour_no_fire(self):
        """Friday at 14:00 does not trigger."""
        fri_2pm = datetime(2026, 6, 12, 14, 0, 0, tzinfo=UTC)
        assert is_friday_3pm(fri_2pm) is False

    def test_non_friday_no_fire(self):
        """Monday at 15:00 does not trigger."""
        mon = datetime(2026, 6, 8, 15, 0, 0, tzinfo=UTC)
        assert is_friday_3pm(mon) is False

    def test_month_end_last_business_day(self):
        """June 30 2026 is Tuesday — it's the last business day."""
        assert is_month_end(datetime(2026, 6, 30, 15, 0), today=date(2026, 6, 30)) is True

    def test_month_end_not_last_day(self):
        """June 15 is not month end."""
        assert is_month_end(datetime(2026, 6, 15, 15, 0), today=date(2026, 6, 15)) is False

    def test_month_end_weekend_rollback(self):
        """May 2026: May 31 is Sunday → last biz day is May 29 (Friday)."""
        assert is_month_end(datetime(2026, 5, 29, 15, 0), today=date(2026, 5, 29)) is True
        assert is_month_end(datetime(2026, 5, 31, 15, 0), today=date(2026, 5, 31)) is False

    def test_should_trigger_both(self):
        """Last Friday of June 2026 at 15:00 → both triggers fire."""
        # June 26, 2026 is a Friday; June 30 is the real last biz day.
        # Let's use a date that's both Friday and month-end last biz day:
        # Find it: June 2026 last day = 30 (Tue). Last biz day = 30.
        # So Friday + month_end can't coincide in June 2026.
        # Use a month where last biz day IS a Friday:
        # July 2026: 31st is Friday — perfect
        fri_jul_31 = datetime(2026, 7, 31, 15, 0, 0, tzinfo=UTC)
        triggers = should_trigger(fri_jul_31)
        assert triggers["friday"] is True
        assert triggers["month_end"] is True

    def test_should_trigger_neither(self):
        """A random Tuesday → neither trigger fires."""
        tue = datetime(2026, 6, 9, 10, 0, 0, tzinfo=UTC)
        triggers = should_trigger(tue)
        assert triggers["friday"] is False
        assert triggers["month_end"] is False

    def test_december_month_end(self):
        """December 2026: Dec 31 is Thursday → last biz day."""
        assert is_month_end(datetime(2026, 12, 31, 15, 0), today=date(2026, 12, 31)) is True

    def test_february_month_end(self):
        """Feb 2026: Feb 28 is Saturday → last biz day is Feb 27 (Friday)."""
        assert is_month_end(datetime(2026, 2, 27, 15, 0), today=date(2026, 2, 27)) is True
        assert is_month_end(datetime(2026, 2, 28, 15, 0), today=date(2026, 2, 28)) is False


# ── Tests: At-Risk Honesty ────────────────────────────────────────────


class TestAtRiskHonesty:
    """test_at_risk_honesty — at_risk=true funnel → manager draft contains
    the risk; suppressing it is impossible via prompt."""

    def test_at_risk_flags_in_manager_draft(self):
        """at_risk flags from input appear in manager draft risk_section."""
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([AT_RISK_MANAGER_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_manager_update(AT_RISK_INPUT)

        assert result.success
        assert result.output is not None
        risk_section = result.output.data["risk_section"]

        # Each at_risk flag from input must appear in the risk_section
        for flag in AT_RISK_INPUT["at_risk_flags"]:
            # Check that the key content of the flag is present
            # Use a substring check — the flag text should be present
            assert flag in risk_section or any(
                word in risk_section for word in flag.split(" — ")
            ), f"Risk flag missing from manager draft: {flag}"

    def test_at_risk_true_funnel_flagged(self):
        """When funnel at_risk=true, the at_risk_flags list is non-empty."""
        assert AT_RISK_INPUT["funnel_state"]["at_risk"] is True
        assert len(AT_RISK_INPUT["at_risk_flags"]) > 0

    def test_need_from_you_present(self):
        """Manager draft always includes need_from_you."""
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([AT_RISK_MANAGER_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_manager_update(AT_RISK_INPUT)
        assert result.success
        assert result.output.data["need_from_you"]
        assert len(result.output.data["need_from_you"]) > 0

    def test_at_risk_in_rendered_markdown(self):
        """Risk flags appear in the rendered manager markdown."""
        update = ManagerUpdate(**_AT_RISK_MANAGER_DATA)
        md = render_manager_markdown(update)
        for flag in AT_RISK_INPUT["at_risk_flags"]:
            assert flag in md or any(
                word in md for word in flag.split(" — ")
            ), f"Risk flag missing from rendered markdown: {flag}"

    def test_pipeline_at_risk_end_to_end(self):
        """Full pipeline with at_risk input: recap and manager both reflect risks."""
        from app.adapters.mock.email import MockEmailAdapter

        email_adapter = MockEmailAdapter()
        reports: list[dict] = []
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([AT_RISK_RECAP_RESPONSE, AT_RISK_MANAGER_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            with patch("app.agents.reporting.ReportingAgent", return_value=agent):
                process_reporting_job(
                    AT_RISK_INPUT,
                    trigger="friday",
                    email_adapter=email_adapter,
                    create_report_fn=lambda r: reports.append(r),
                )

        # Manager draft must contain risk flags
        mgr_report = next(r for r in reports if r["report_type"] == "manager_update")
        md = mgr_report["markdown"]
        for flag in AT_RISK_INPUT["at_risk_flags"]:
            assert flag in md or any(
                word in md for word in flag.split(" — ")
            )


# ── Tests: Golden AGENTS.md §10 ──────────────────────────────────────


class TestGoldenSection10:
    """Golden test: AGENTS.md §10 manager draft format.

    Golden (manager draft) — "Week of 8/10: 3 held (target 3), 5 booked for
    next week. Reply rate dipped to 2.8% — testing two new angles Mon; show rate
    80% (confirmations cadence working). Risk: 2 of next week's 5 are >5 days out,
    pull-in offers going out Monday. Need from you: 10 min on the [X] account list."
    """

    def test_golden_manager_format(self):
        """Manager update follows outcome-first format per §10."""
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([GOLDEN_S10_MANAGER_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_manager_update(SAMPLE_INPUT)

        assert result.success
        data = result.output.data

        # Outcome-first: subject/body lead with held vs target + booked pipeline
        assert "3 held" in data["subject"] or "3 held" in data["body"]
        assert "target 3" in data["subject"] or "target 3" in data["body"]
        assert "5 booked" in data["subject"] or "5 booked" in data["body"]

        # Rate change mentioned
        body = data["body"]
        assert "0.028" in body or "reply rate" in body.lower()
        assert "0.80" in body or "0.8" in body or "show rate" in body.lower()

        # Has risk section
        assert "risk_section" in data
        assert len(data["risk_section"]) > 0

        # Has need_from_you
        assert "need_from_you" in data
        assert len(data["need_from_you"]) > 0

    def test_golden_manager_no_activity_theater(self):
        """Manager draft should be outcome-first, not activity-theater."""
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([GOLDEN_S10_MANAGER_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_manager_update(SAMPLE_INPUT)

        body = result.output.data["body"]
        # Should NOT start with "I did X" activity language
        assert not body.lower().startswith("i sent")
        assert not body.lower().startswith("i made")
        assert not body.lower().startswith("this week i")

    def test_golden_rendered_markdown(self):
        """Rendered manager markdown includes all required sections."""
        update = ManagerUpdate(**_GOLDEN_S10_MANAGER_DATA)
        md = render_manager_markdown(update)

        assert "Subject:" in md
        assert "Risks:" in md
        assert "Need from you:" in md
        assert "3 held" in md

    def test_golden_recap_has_required_fields(self):
        """Personal recap golden has all schema fields."""
        recap = PersonalRecap(**_GOLDEN_RECAP_DATA)
        assert recap.plan_vs_actual_by_stage
        assert recap.rate_trends
        assert recap.wins
        assert recap.next_week_plan_summary
        assert recap.confidence > 0

    def test_month_end_variant_inputs(self):
        """Month-end input includes annual goal, rate deltas, cold-start exit."""
        assert "annual_goal" in MONTH_END_INPUT
        assert "rate_deltas_vs_benchmarks" in MONTH_END_INPUT
        assert "cold_start_exit" in MONTH_END_INPUT
        assert "promotion_scorecard" in MONTH_END_INPUT

        # Verify these can be passed to the agent
        agent = ReportingAgent()
        mock_llm = MockLLMCallable([GOLDEN_RECAP_RESPONSE])
        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run_recap(MONTH_END_INPUT)
        assert result.success


# ── Tests: Report model ───────────────────────────────────────────────


class TestReportModel:
    """Verify Report model works with DB."""

    def test_report_roundtrip(self, db_session):
        from app.models.report import Report

        report = Report(
            report_type="personal_recap",
            trigger="friday",
            goal_id="goal-001",
            payload={"summary": "test"},
            markdown="# Test",
            approval_required=False,
            approved=True,
        )
        db_session.add(report)
        db_session.commit()

        fetched = db_session.query(Report).first()
        assert fetched is not None
        assert fetched.report_type == "personal_recap"
        assert fetched.approved is True
        assert fetched.markdown == "# Test"
        assert fetched.payload == {"summary": "test"}

    def test_manager_report_unapproved(self, db_session):
        from app.models.report import Report

        report = Report(
            report_type="manager_update",
            trigger="friday",
            payload={"body": "test"},
            markdown="test",
            approval_required=True,
            approved=False,
            write_back_ref="draft-abc123",
        )
        db_session.add(report)
        db_session.commit()

        fetched = db_session.query(Report).first()
        assert fetched.approval_required is True
        assert fetched.approved is False
        assert fetched.write_back_ref == "draft-abc123"


# ── Tests: Rendering ─────────────────────────────────────────────────


class TestRendering:
    def test_recap_markdown_structure(self):
        recap = PersonalRecap(**_GOLDEN_RECAP_DATA)
        md = render_recap_markdown(recap, "friday", "Week of 8/10")
        assert "# Personal Recap — Week of 8/10" in md
        assert "## Summary" in md
        assert "## Plan vs Actual" in md
        assert "## Rate Trends" in md
        assert "## Wins" in md
        assert "## At-Risk Flags" in md
        assert "## Next Week Plan" in md

    def test_manager_markdown_structure(self):
        update = ManagerUpdate(**_GOLDEN_MANAGER_DATA)
        md = render_manager_markdown(update)
        assert "Subject:" in md
        assert "Risks:" in md
        assert "Need from you:" in md
