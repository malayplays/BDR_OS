"""Tests for the CRM Scribe Agent (Session 9).

Required tests (merge bar):
- test_split_gate — one run produces an auto-written note AND queued
  next_steps; mock CRM written[] shows note only until approval.
- test_stage_never_auto — transcript with obvious qualification →
  output has s1_candidate flag only; no s1_reached/ad_accepted event.
- test_three_fixture_transcripts — discovery/objection-heavy/no-show
  transcripts each produce schema-valid output; objection-heavy yields
  next_step containing objection follow-up.
- test_field_patch_safety — patch touching a non-whitelisted CRM field
  → REQUIRE_APPROVAL policy flag.
- Golden test for AGENTS.md §8.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.agents.crm_scribe import (
    CRMScribeAgent,
    classify_patch_fields,
    process_crm_scribe_job,
)

# ── Fixtures ──────────────────────────────────────────────────────────

DISCOVERY_TRANSCRIPT = {
    "call_ref": "call-001",
    "segments": [
        {
            "speaker": "rep",
            "text": "Thanks for joining. I saw your team just raised Series B — congrats.",
            "start_seconds": 0.0,
            "end_seconds": 4.1,
        },
        {
            "speaker": "prospect",
            "text": "Thanks! Yeah, we're hiring like crazy right now.",
            "start_seconds": 5.6,
            "end_seconds": 8.5,
        },
        {
            "speaker": "rep",
            "text": (
                "That's usually when onboarding becomes a bottleneck. "
                "How are you handling new eng ramp-up?"
            ),
            "start_seconds": 9.1,
            "end_seconds": 14.5,
        },
        {
            "speaker": "prospect",
            "text": "It's painful honestly. Seniors spend weeks onboarding each new hire.",
            "start_seconds": 15.6,
            "end_seconds": 19.7,
        },
        {
            "speaker": "rep",
            "text": "That's exactly what Devin helps with. Can I show you a quick example?",
            "start_seconds": 21.0,
            "end_seconds": 25.2,
        },
        {
            "speaker": "prospect",
            "text": "Sure, that'd be useful.",
            "start_seconds": 26.3,
            "end_seconds": 27.7,
        },
    ],
}

OBJECTION_TRANSCRIPT = {
    "call_ref": "call-002",
    "segments": [
        {
            "speaker": "rep",
            "text": (
                "Appreciate you taking the time. "
                "How's your team thinking about developer productivity?"
            ),
            "start_seconds": 0.0,
            "end_seconds": 5.2,
        },
        {
            "speaker": "prospect",
            "text": "We already have Copilot Enterprise. Not sure we need another tool.",
            "start_seconds": 6.2,
            "end_seconds": 10.2,
        },
        {
            "speaker": "rep",
            "text": (
                "Totally fair. Copilot is great for autocomplete. "
                "Devin is different — it takes a whole ticket."
            ),
            "start_seconds": 11.9,
            "end_seconds": 17.5,
        },
        {
            "speaker": "prospect",
            "text": "I don't know, our devs are pretty set in their ways.",
            "start_seconds": 19.4,
            "end_seconds": 22.5,
        },
        {
            "speaker": "rep",
            "text": "What if I showed you a side-by-side on one of your real tickets?",
            "start_seconds": 23.1,
            "end_seconds": 26.9,
        },
        {
            "speaker": "prospect",
            "text": "Maybe. Send me something and I'll look at it.",
            "start_seconds": 28.7,
            "end_seconds": 31.4,
        },
    ],
}

NO_SHOW_TRANSCRIPT = {
    "call_ref": "call-003",
    "segments": [
        {
            "speaker": "rep",
            "text": "Hi — we had you on the calendar for 2pm, wanted to check in.",
            "start_seconds": 0.0,
            "end_seconds": 3.6,
        },
        {
            "speaker": "prospect",
            "text": "Oh man, I'm so sorry. Got pulled into a fire drill.",
            "start_seconds": 4.1,
            "end_seconds": 7.2,
        },
        {
            "speaker": "rep",
            "text": "No worries at all. How's Thursday at the same time?",
            "start_seconds": 8.2,
            "end_seconds": 11.3,
        },
        {
            "speaker": "prospect",
            "text": "Thursday works. Sorry again.",
            "start_seconds": 12.9,
            "end_seconds": 14.6,
        },
    ],
}

ACCOUNT = {
    "ref": "acct-100",
    "name": "VercelLike",
    "domain": "vercellike.com",
    "tier": "standard",
    "owner": "malay",
    "custom": {},
}

CONTACT = {
    "ref": "contact-001",
    "account_ref": "acct-100",
    "name": "Jane Smith",
    "title": "VP Engineering",
    "email": "jane@vercellike.com",
}

MEETING = {
    "ref": "mtg-001",
    "title": "Discovery call",
    "occurred_at": "2026-06-05T14:00:00",
}


def _make_job_input(transcript=None, account=None, contact=None, meeting=None):
    return {
        "transcript": transcript or DISCOVERY_TRANSCRIPT,
        "meeting": meeting or MEETING,
        "account": account or ACCOUNT,
        "contact": contact or CONTACT,
    }


# ── Golden LLM mock responses ────────────────────────────────────────

_DISCOVERY_GOLDEN = {
    "summary": [
        "Prospect raised Series B, hiring aggressively",
        "Onboarding is a pain point — seniors spend weeks per new hire",
        "Expressed interest in seeing Devin demo",
        "ICP fit confirmed: growing eng team, dev productivity need",
        "Agreed to follow-up demo session",
    ],
    "sql_checklist": {
        "icp_fit": True,
        "relevant_title": True,
        "expressed_pain": True,
        "confirmed_need": True,
        "next_steps_agreed": True,
        "eval_timeline_6mo": False,
        "facts_verified": True,
    },
    "three_whys": {
        "anything": "Seniors spend weeks onboarding each new hire",
        "now": "Series B raise + aggressive hiring = immediate pain",
        "windsurf_devin": "Devin absorbs grunt-work backlog during hiring ramp",
    },
    "next_steps": [
        {
            "action": "Schedule Devin demo session",
            "owner": "rep",
            "due": "2026-06-10",
        },
        {
            "action": "Confirm eval timeline",
            "owner": "rep",
            "due": "2026-06-12",
        },
    ],
    "crm_fields_patch": {
        "last_call_date": "2026-06-05",
        "last_call_summary": "Discovery: hiring pain, interested in demo",
        "qualification_status": "discovery_complete",
        "s1_candidate": True,
    },
    "provenance_note": (
        "Outbound touch: discovery call 2026-06-05, rep-initiated via sequence. "
        "Contact sourced from Named Target list."
    ),
    "s1_candidate": True,
    "confidence": 0.88,
    "needs_human_because": None,
}

_OBJECTION_GOLDEN = {
    "summary": [
        "Prospect has Copilot Enterprise, skeptical of additional tool",
        "Developer team resistant to change",
        "Rep reframed: autocomplete vs whole-ticket delegation",
        "Prospect lukewarm — agreed to receive materials",
        "No strong commitment, needs follow-up with proof",
    ],
    "sql_checklist": {
        "icp_fit": True,
        "relevant_title": True,
        "expressed_pain": False,
        "confirmed_need": False,
        "next_steps_agreed": False,
        "eval_timeline_6mo": False,
        "facts_verified": True,
    },
    "three_whys": {
        "anything": "Already has Copilot — not seeing incremental value",
        "now": None,
        "windsurf_devin": None,
    },
    "next_steps": [
        {
            "action": (
                "Send side-by-side comparison on real ticket "
                "(objection follow-up)"
            ),
            "owner": "rep",
            "due": "2026-06-10",
        },
        {
            "action": "Follow up on materials sent",
            "owner": "rep",
            "due": "2026-06-15",
        },
    ],
    "crm_fields_patch": {
        "last_call_date": "2026-06-08",
        "last_call_summary": "Objection-heavy: has Copilot, devs set in ways",
        "notes": "Key objection: already have Copilot Enterprise",
    },
    "provenance_note": (
        "Outbound touch: call 2026-06-08, rep-initiated. "
        "Objection logged: existing Copilot Enterprise deployment."
    ),
    "s1_candidate": False,
    "confidence": 0.65,
    "needs_human_because": None,
}

_NO_SHOW_GOLDEN = {
    "summary": [
        "Prospect no-showed original 2pm slot",
        "Reason: pulled into fire drill",
        "Rescheduled to Thursday same time",
        "Prospect apologetic, still interested",
        "Brief exchange, no qualification content",
    ],
    "sql_checklist": {
        "icp_fit": False,
        "relevant_title": False,
        "expressed_pain": False,
        "confirmed_need": False,
        "next_steps_agreed": True,
        "eval_timeline_6mo": False,
        "facts_verified": False,
    },
    "three_whys": {
        "anything": None,
        "now": None,
        "windsurf_devin": None,
    },
    "next_steps": [
        {
            "action": "Confirm Thursday reschedule",
            "owner": "rep",
            "due": "2026-06-12",
        },
    ],
    "crm_fields_patch": {
        "last_call_date": "2026-06-11",
        "last_call_summary": "No-show, rescheduled to Thursday",
        "next_step": "Rescheduled call Thursday",
        "next_step_date": "2026-06-12",
    },
    "provenance_note": (
        "No-show recovery: prospect missed 2pm, rescheduled Thursday same time."
    ),
    "s1_candidate": False,
    "confidence": 0.92,
    "needs_human_because": None,
}

_STAGE_ATTEMPT_GOLDEN = {
    **_DISCOVERY_GOLDEN,
    "s1_reached": True,
    "ad_accepted": True,
    "crm_fields_patch": {
        **_DISCOVERY_GOLDEN["crm_fields_patch"],
        "s1_reached": True,
    },
    "s1_candidate": True,
}

_BAD_PATCH_GOLDEN = {
    **_DISCOVERY_GOLDEN,
    "crm_fields_patch": {
        "last_call_date": "2026-06-05",
        "opportunity_amount": 500000,
        "close_date": "2026-09-01",
    },
}


def _mock_llm_response(golden_data: dict):
    """Create a mock for AgentBase._call_llm returning golden data."""
    response_json = json.dumps(golden_data)

    def _call_llm(self, system, user_message):
        return response_json, 500, 200

    return _call_llm


# ── Tests ─────────────────────────────────────────────────────────────


class TestSplitGate:
    """test_split_gate — one run produces an auto-written note AND
    queued next_steps; mock CRM written[] shows note only until
    approval."""

    def test_split_gate(self):
        from app.adapters.mock.crm import MockCRMAdapter

        crm = MockCRMAdapter()
        job_input = _make_job_input()

        with patch.object(
            CRMScribeAgent, "_call_llm", _mock_llm_response(_DISCOVERY_GOLDEN)
        ):
            result = process_crm_scribe_job(
                job_input,
                crm_adapter=crm,
            )

        assert result.success
        assert result.output is not None

        # CRM should have the auto-approved note written
        note_writes = [
            w for w in crm.written if w["type"] == "log_activity"
        ]
        assert len(note_writes) == 1
        note = note_writes[0]
        assert note["data"]["subject"] == "[BDR-OS] Call scribe"
        assert "Call Summary" in note["data"]["body"]
        assert "Qualification" in note["data"]["body"]

        # No tasks should be written yet (they're gated)
        task_writes = [
            w for w in crm.written if w["type"] == "create_task"
        ]
        assert len(task_writes) == 0

        # Gated part should be queued in output
        assert "_gated" in result.output.data
        gated = result.output.data["_gated"]
        assert len(gated["next_steps"]) == 2
        assert gated["crm_fields_patch"]["s1_candidate"] is True


class TestStageNeverAuto:
    """test_stage_never_auto — transcript with obvious qualification →
    output has s1_candidate flag only; no s1_reached/ad_accepted."""

    def test_stage_never_auto(self):
        job_input = _make_job_input()

        with patch.object(
            CRMScribeAgent,
            "_call_llm",
            _mock_llm_response(_STAGE_ATTEMPT_GOLDEN),
        ):
            result = process_crm_scribe_job(job_input)

        assert result.success
        assert result.output is not None
        data = result.output.data

        # s1_candidate is allowed — it's a proposal flag
        assert data.get("s1_candidate") is True

        # Stage-advancement fields MUST be stripped
        assert "s1_reached" not in data
        assert "s2_reached" not in data
        assert "ad_accepted" not in data

        # Also stripped from crm_fields_patch
        patch_data = data.get("crm_fields_patch", {})
        assert "s1_reached" not in patch_data
        assert "s2_reached" not in patch_data
        assert "ad_accepted" not in patch_data


class TestThreeFixtureTranscripts:
    """test_three_fixture_transcripts — discovery/objection-heavy/
    no-show transcripts each produce schema-valid output;
    objection-heavy yields next_step containing objection follow-up."""

    @pytest.mark.parametrize(
        "transcript,golden,call_type",
        [
            (DISCOVERY_TRANSCRIPT, _DISCOVERY_GOLDEN, "discovery"),
            (OBJECTION_TRANSCRIPT, _OBJECTION_GOLDEN, "objection"),
            (NO_SHOW_TRANSCRIPT, _NO_SHOW_GOLDEN, "no_show"),
        ],
        ids=["discovery", "objection-heavy", "no-show-reschedule"],
    )
    def test_three_fixture_transcripts(
        self, transcript, golden, call_type
    ):
        job_input = _make_job_input(transcript=transcript)

        with patch.object(
            CRMScribeAgent, "_call_llm", _mock_llm_response(golden)
        ):
            result = process_crm_scribe_job(job_input)

        assert result.success
        assert result.output is not None
        data = result.output.data

        # Schema validation: all required fields present
        assert len(data["summary"]) <= 5
        assert len(data["summary"]) > 0

        checklist = data["sql_checklist"]
        assert isinstance(checklist, dict)
        expected_keys = {
            "icp_fit",
            "relevant_title",
            "expressed_pain",
            "confirmed_need",
            "next_steps_agreed",
            "eval_timeline_6mo",
            "facts_verified",
        }
        assert expected_keys == set(checklist.keys())

        whys = data["three_whys"]
        assert isinstance(whys, dict)
        assert set(whys.keys()) == {"anything", "now", "windsurf_devin"}

        assert isinstance(data["next_steps"], list)
        assert isinstance(data["crm_fields_patch"], dict)
        assert isinstance(data["provenance_note"], str)

        # Objection-heavy: must have objection follow-up next_step
        if call_type == "objection":
            next_step_texts = [
                (
                    s["action"].lower()
                    if isinstance(s, dict)
                    else s.action.lower()
                )
                for s in data["next_steps"]
            ]
            has_followup = any(
                "objection" in t or "follow-up" in t or "follow up" in t
                for t in next_step_texts
            )
            assert has_followup, (
                "Objection-heavy transcript must produce next_step "
                f"with objection follow-up, got: {next_step_texts}"
            )


class TestFieldPatchSafety:
    """test_field_patch_safety — patch touching a non-whitelisted CRM
    field → REQUIRE_APPROVAL policy flag."""

    def test_field_patch_safety(self):
        job_input = _make_job_input()

        with patch.object(
            CRMScribeAgent,
            "_call_llm",
            _mock_llm_response(_BAD_PATCH_GOLDEN),
        ):
            result = process_crm_scribe_job(job_input)

        assert result.success
        assert result.output is not None
        data = result.output.data

        # Policy flags should indicate REQUIRE_APPROVAL
        policy_flags = data.get("_policy_flags", {})
        assert policy_flags.get("REQUIRE_APPROVAL") is True

        # Non-whitelisted fields should be identified
        non_wl = policy_flags.get("non_whitelisted_fields", [])
        assert "opportunity_amount" in non_wl
        assert "close_date" in non_wl

    def test_classify_patch_fields_whitelisted(self):
        p = {"last_call_date": "2026-06-05", "notes": "test"}
        all_wl, non_wl = classify_patch_fields(p)
        assert all_wl is True
        assert non_wl == []

    def test_classify_patch_fields_non_whitelisted(self):
        p = {"last_call_date": "2026-06-05", "close_date": "2026-09-01"}
        all_wl, non_wl = classify_patch_fields(p)
        assert all_wl is False
        assert "close_date" in non_wl


class TestGoldenScribe:
    """Golden test for AGENTS.md §8 — discovery transcript →
    sql_checklist 6/7 true (eval_timeline unconfirmed → next_step
    to confirm it), three_whys from prospect's own words,
    s1_candidate: true."""

    def test_golden_discovery(self):
        from app.adapters.mock.crm import MockCRMAdapter

        crm = MockCRMAdapter()
        job_input = _make_job_input()

        with patch.object(
            CRMScribeAgent,
            "_call_llm",
            _mock_llm_response(_DISCOVERY_GOLDEN),
        ):
            result = process_crm_scribe_job(
                job_input,
                crm_adapter=crm,
            )

        assert result.success
        assert result.output is not None
        data = result.output.data

        # sql_checklist: 6/7 true (eval_timeline_6mo is false)
        checklist = data["sql_checklist"]
        true_count = sum(1 for v in checklist.values() if v)
        assert true_count == 6, (
            f"Expected 6/7 SQL checklist items true, got {true_count}/7"
        )
        assert checklist["eval_timeline_6mo"] is False

        # next_step to confirm eval timeline
        next_step_texts = [
            (
                s["action"].lower()
                if isinstance(s, dict)
                else s.action.lower()
            )
            for s in data["next_steps"]
        ]
        has_eval = any(
            "eval timeline" in t or "confirm eval" in t
            for t in next_step_texts
        )
        assert has_eval, (
            "Golden: must have next_step to confirm eval timeline, "
            f"got: {next_step_texts}"
        )

        # three_whys filled from prospect's own words
        whys = data["three_whys"]
        assert whys["anything"] is not None and len(whys["anything"]) > 0
        assert whys["now"] is not None and len(whys["now"]) > 0
        assert (
            whys["windsurf_devin"] is not None
            and len(whys["windsurf_devin"]) > 0
        )

        # s1_candidate: true for review — NOT s1_reached
        assert data["s1_candidate"] is True
        assert "s1_reached" not in data
        assert "ad_accepted" not in data

        # CRM note was written (auto-approved)
        note_writes = [
            w for w in crm.written if w["type"] == "log_activity"
        ]
        assert len(note_writes) == 1
        body = note_writes[0]["data"]["body"]
        assert "s1_candidate" in body

        # Provenance note present
        assert "provenance_note" in data
        assert len(data["provenance_note"]) > 0
