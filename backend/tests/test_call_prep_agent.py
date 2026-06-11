"""Tests for the Call Prep Agent (Session 8).

Required tests (merge bar):
- test_card_schema_and_budget — all fields present, ≤600 chars.
- test_graceful_degradation — cold account (no brief/thread/transcript) → valid card, gaps marked.
- test_continuity — fixture with prior "we have Copilot" exchange → last_interaction references it
  and instructs continuation.
- test_timing — fake clock: card job created at T−30min, attached to the meeting's Today entry.
- Golden test for AGENTS.md §7.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

from app.agents.call_prep import (
    CallPrepAgent,
    CallPrepCard,
    build_call_prep_job,
    process_call_prep_job,
    should_trigger_call_prep,
)

# ── Fixtures ──────────────────────────────────────────────────────────

MEETING = {
    "ref": "mtg-001",
    "title": "Discovery Call — VercelLike",
    "start": "2026-06-11T14:00:00",
    "end": "2026-06-11T14:30:00",
    "account_ref": "acct-100",
    "contact_ref": "contact-001",
    "contact_name": "Jane Smith",
    "contact_title": "VP Engineering",
    "company": "VercelLike",
}

BRIEF = {
    "company_snapshot": [
        "Series C, 200-1000 employees, TypeScript/Rust stack",
        "Engineering headcount growing 40% QoQ",
        "Currently uses Copilot Enterprise",
    ],
    "why_now": "Eng team growing 40% QoQ with 27 open backend reqs.",
    "angle": "Frame Devin as headcount-multiplier during hiring crunch.",
    "landmines": "Already pay for Copilot Enterprise.",
}

SIGNAL = {
    "kind": "hiring_surge",
    "account_domain": "vercellike.com",
    "strength": 0.85,
    "evidence": "27 open backend roles, +40% eng headcount QoQ",
}

THREAD_HISTORY_WITH_OBJECTION = [
    {
        "id": "msg-001",
        "sender": "malay@company.com",
        "body": "Teams like yours are using Devin to keep velocity flat through hiring waves.",
        "sent_at": "2026-06-02T10:00:00",
    },
    {
        "id": "msg-002",
        "sender": "jane@vercellike.com",
        "body": "We already have Copilot — not sure we need another AI tool.",
        "sent_at": "2026-06-02T14:30:00",
    },
    {
        "id": "msg-003",
        "sender": "malay@company.com",
        "body": "Copilot autocompletes lines; Devin handles whole tickets end-to-end. "
        "Happy to show a side-by-side on one real ticket from your backlog.",
        "sent_at": "2026-06-02T15:00:00",
    },
]

TRANSCRIPT_REFS = [
    {
        "call_ref": "call-001",
        "title": "Initial outreach call",
        "occurred_at": "2026-05-15T10:00:00",
        "summary": "Quick intro, discussed team growth plans.",
    },
]

FUNNEL_CONTEXT = {
    "stage": "create",
    "touches": 3,
    "days_in_stage": 9,
}

# ── Golden LLM Mock Responses ─────────────────────────────────────────

_GOLDEN_CARD_DATA = {
    "who": "Jane Smith, VP Eng @ VercelLike\nSeries C, 200-1000 eng, growing 40% QoQ",
    "why_now": "27 open backend reqs — onboarding pain is acute now.",
    "last_interaction": "She said 'we have Copilot' on 6/2 — you reframed to ticket-delegation; pick up that thread.",
    "goal_of_call": "Get her to agree to a side-by-side demo on one of their tickets.",
    "likely_objections": [
        {"objection": "We have Copilot", "response": "Copilot autocompletes; Devin owns whole tickets."},
        {"objection": "No bandwidth to eval", "response": "15-min demo on your ticket, zero prep needed."},
    ],
    "the_one_thing_to_show": "Devin solving a real ticket from their backlog end-to-end.",
    "confidence": 0.88,
    "needs_human_because": None,
}
GOLDEN_RESPONSE = json.dumps(_GOLDEN_CARD_DATA)

_COLD_ACCOUNT_CARD_DATA = {
    "who": "Bob Lee, Director Platform @ ColdCorp\nMid-market SaaS, no prior relationship",
    "why_now": "New eng leadership hire signals tooling review.",
    "last_interaction": "(no prior contact)",
    "goal_of_call": "Qualify: confirm team size and current dev tooling stack.",
    "likely_objections": [
        {"objection": "Who are you?", "response": "Quick intro — saw the VP Eng hire, relevant timing."},
        {"objection": "We're set on tools", "response": "Most teams say that — 15 min to see something new?"},
    ],
    "the_one_thing_to_show": "Devin handling a multi-file refactor autonomously.",
    "confidence": 0.65,
    "needs_human_because": None,
}
COLD_ACCOUNT_RESPONSE = json.dumps(_COLD_ACCOUNT_CARD_DATA)

_CONTINUITY_CARD_DATA = {
    "who": "Jane Smith, VP Eng @ VercelLike\nSeries C, growing 40% QoQ",
    "why_now": "27 open backend reqs — hiring pain is immediate.",
    "last_interaction": (
        "She replied 'we have Copilot' on 6/2 — you reframed to ticket-delegation;"
        " open by picking that thread up, don't restart pitch."
    ),
    "goal_of_call": "Side-by-side demo on a real ticket from their backlog.",
    "likely_objections": [
        {"objection": "Still not sure vs Copilot", "response": "Show, don't tell — let's run one ticket live."},
        {"objection": "No time for eval", "response": "Zero prep: pick any open PR, Devin runs it now."},
    ],
    "the_one_thing_to_show": "Live ticket-delegation: Devin takes a real issue end-to-end.",
    "confidence": 0.90,
    "needs_human_because": None,
}
CONTINUITY_RESPONSE = json.dumps(_CONTINUITY_CARD_DATA)


def _make_job_input(
    meeting=None,
    brief=None,
    thread_history=None,
    transcript_refs=None,
    signal=None,
    funnel_context=None,
):
    inp: dict = {"meeting": meeting or MEETING}
    if brief is not None:
        inp["brief"] = brief
    if thread_history is not None:
        inp["thread_history"] = thread_history
    if transcript_refs is not None:
        inp["transcript_refs"] = transcript_refs
    if signal is not None:
        inp["signal"] = signal
    if funnel_context is not None:
        inp["funnel_context"] = funnel_context
    return inp


def _mock_llm_response(response_text: str):
    """Create a mock for _call_llm that returns the given response."""

    def _mock(self, system, user_message):
        return response_text, 500, 200

    return _mock


# ── Tests ─────────────────────────────────────────────────────────────


class TestCardSchemaAndBudget:
    """test_card_schema_and_budget — all fields present, ≤600 chars."""

    @patch.object(CallPrepAgent, "_call_llm", _mock_llm_response(GOLDEN_RESPONSE))
    def test_card_schema_and_budget(self):
        job_input = _make_job_input(
            brief=BRIEF,
            thread_history=THREAD_HISTORY_WITH_OBJECTION,
            transcript_refs=TRANSCRIPT_REFS,
            signal=SIGNAL,
            funnel_context=FUNNEL_CONTEXT,
        )
        result = process_call_prep_job(job_input)

        assert result.success
        assert result.output is not None

        # Validate all fields present by reconstructing the card
        card = CallPrepCard.model_validate(
            {
                "confidence": result.output.confidence,
                "needs_human_because": result.output.needs_human_because,
                **result.output.data,
            }
        )
        assert card.who
        assert card.why_now
        assert card.last_interaction
        assert card.goal_of_call
        assert len(card.likely_objections) == 2
        assert card.the_one_thing_to_show
        assert card.confidence > 0

        # Budget check: ≤600 chars
        rendered = card.render()
        assert len(rendered) <= 600, f"Card is {len(rendered)} chars, exceeds 600"


class TestGracefulDegradation:
    """test_graceful_degradation — cold account (no brief/thread/transcript) → valid card, gaps marked."""

    @patch.object(CallPrepAgent, "_call_llm", _mock_llm_response(COLD_ACCOUNT_RESPONSE))
    def test_graceful_degradation(self):
        cold_meeting = {
            "ref": "mtg-002",
            "title": "Cold Call — ColdCorp",
            "start": "2026-06-11T15:00:00",
            "end": "2026-06-11T15:15:00",
            "account_ref": "acct-300",
            "contact_ref": "contact-010",
            "contact_name": "Bob Lee",
            "contact_title": "Director of Platform",
            "company": "ColdCorp",
        }
        # No brief, no thread_history, no transcript_refs
        job_input = _make_job_input(meeting=cold_meeting)
        result = process_call_prep_job(job_input)

        assert result.success
        assert result.output is not None

        card = CallPrepCard.model_validate(
            {
                "confidence": result.output.confidence,
                "needs_human_because": result.output.needs_human_because,
                **result.output.data,
            }
        )
        # Gaps marked
        assert "(no prior contact)" in card.last_interaction
        # Still a valid card
        assert card.who
        assert card.why_now
        assert card.goal_of_call
        assert len(card.likely_objections) == 2
        assert card.the_one_thing_to_show
        # Budget
        assert card.char_count() <= 600


class TestContinuity:
    """test_continuity — fixture with prior 'we have Copilot' exchange →
    last_interaction references it and instructs continuation."""

    @patch.object(CallPrepAgent, "_call_llm", _mock_llm_response(CONTINUITY_RESPONSE))
    def test_continuity(self):
        job_input = _make_job_input(
            brief=BRIEF,
            thread_history=THREAD_HISTORY_WITH_OBJECTION,
            transcript_refs=TRANSCRIPT_REFS,
            signal=SIGNAL,
            funnel_context=FUNNEL_CONTEXT,
        )
        result = process_call_prep_job(job_input)

        assert result.success
        assert result.output is not None

        card = CallPrepCard.model_validate(
            {
                "confidence": result.output.confidence,
                "needs_human_because": result.output.needs_human_because,
                **result.output.data,
            }
        )
        # Continuity rule: must reference prior objection
        li = card.last_interaction.lower()
        assert "copilot" in li, "last_interaction must reference prior Copilot objection"
        # Must instruct continuation, not restart
        assert any(phrase in li for phrase in ["pick up", "picking up", "continue", "don't restart", "not restart"]), (
            "last_interaction must instruct picking up the thread, not restarting"
        )


class TestTiming:
    """test_timing — fake clock: card job created at T−30min, attached to the meeting's Today entry."""

    def test_trigger_at_t_minus_30(self):
        meeting_start = datetime(2026, 6, 11, 14, 0, 0)
        # Exactly T-30min
        now_30 = meeting_start - timedelta(minutes=30)
        assert should_trigger_call_prep(meeting_start, now_30) is True

    def test_no_trigger_before_t_minus_30(self):
        meeting_start = datetime(2026, 6, 11, 14, 0, 0)
        # T-31min — too early
        now_31 = meeting_start - timedelta(minutes=31)
        assert should_trigger_call_prep(meeting_start, now_31) is False

    def test_trigger_after_t_minus_30(self):
        meeting_start = datetime(2026, 6, 11, 14, 0, 0)
        # T-15min — already past trigger window
        now_15 = meeting_start - timedelta(minutes=15)
        assert should_trigger_call_prep(meeting_start, now_15) is True

    def test_job_attached_to_today_entry(self):
        meeting_start = datetime(2026, 6, 11, 14, 0, 0)
        job = build_call_prep_job(
            MEETING,
            brief=BRIEF,
            thread_history=THREAD_HISTORY_WITH_OBJECTION,
            signal=SIGNAL,
            meeting_start=meeting_start,
        )
        # Job is ephemeral
        assert job["ephemeral"] is True
        # Job is auto-approved
        assert job["approval_gate"] == "auto"
        # Job attached to meeting's Today entry
        assert job["today_entry_ref"] == MEETING["ref"]
        # Trigger is T-30min timer
        assert job["trigger"]["rule"] == "T-30min before meeting"
        assert job["trigger"]["meeting_ref"] == "mtg-001"
        # Job type
        assert job["job_type"] == "call_prep"
        assert job["agent"] == "call_prep"
        # Input payload contains the meeting
        assert job["input_payload"]["meeting"] == MEETING


class TestGolden:
    """Golden test for AGENTS.md §7 — pre-meeting card where last_interaction
    says: 'He replied "we have Copilot" on 6/2 — you reframed to ticket-delegation;
    open by picking that thread up, don't restart pitch.'"""

    @patch.object(CallPrepAgent, "_call_llm", _mock_llm_response(GOLDEN_RESPONSE))
    def test_golden_agents_md_section_7(self):
        job_input = _make_job_input(
            brief=BRIEF,
            thread_history=THREAD_HISTORY_WITH_OBJECTION,
            transcript_refs=TRANSCRIPT_REFS,
            signal=SIGNAL,
            funnel_context=FUNNEL_CONTEXT,
        )
        result = process_call_prep_job(job_input)

        assert result.success
        assert result.output is not None

        card = CallPrepCard.model_validate(
            {
                "confidence": result.output.confidence,
                "needs_human_because": result.output.needs_human_because,
                **result.output.data,
            }
        )

        # Golden assertions per AGENTS.md §7:
        # - Card has all required fields
        assert card.who
        assert card.why_now
        assert card.last_interaction
        assert card.goal_of_call
        assert len(card.likely_objections) == 2
        assert card.the_one_thing_to_show

        # - last_interaction references Copilot objection and instructs continuation
        li = card.last_interaction.lower()
        assert "copilot" in li
        assert "6/2" in card.last_interaction or "6/2" in li
        assert any(phrase in li for phrase in ["ticket-delegation", "ticket delegation", "reframe"])
        assert any(phrase in li for phrase in ["pick up", "picking up", "pick that thread", "don't restart"])

        # - Budget ≤600 chars
        assert card.char_count() <= 600

        # - Auto-approved (no needs_human_because)
        assert card.needs_human_because is None

        # - Confidence is reasonable
        assert card.confidence >= 0.7
