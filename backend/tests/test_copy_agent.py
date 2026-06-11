"""Tests for the Copy Agent (Session 4).

Required tests (merge bar):
- test_copypack_schema — word/char limits enforced via regeneration not truncation;
  3 distinct angles present (assert rationale labels).
- test_gate_unbypassable — attempts to whitelist outreach_draft → boot failure.
- test_refuses_without_angle — Golden 3 behavior.
- test_rejection_feedback_in_prompt — seed 2 rejections; assert their text reaches
  the prompt assembly.
- test_draft_not_send — write-back calls create_draft; send path raises if reached
  during DRAFT_ONLY.
- Golden tests for AGENTS.md §2 examples (deterministic mock + live-LLM flag).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import AgentOutput, AgentRunResult
from app.agents.copy import (
    DRAFT_ONLY,
    MANDATED_ANGLES,
    CopyAgent,
    CopyPack,
    build_crm_activity,
    build_writeback_draft,
    process_copy_writeback,
)

# ── Fixtures ──────────────────────────────────────────────────────────

HIRING_SURGE_BRIEF = {
    "company_snapshot": [
        "Series C, 200-1000 employees, TypeScript/Rust stack",
        "Engineering headcount growing 40% QoQ",
        "Currently uses Copilot Enterprise",
    ],
    "why_now": (
        "Eng team growing 40% QoQ with 27 open backend reqs — "
        "onboarding/velocity pain is now."
    ),
    "who_to_contact": [
        {"name": "Jane Smith", "title": "VP Engineering", "reason": "VP-level buyer"},
    ],
    "angle": (
        "Frame Devin as headcount-multiplier during the hiring crunch, "
        "not headcount replacement."
    ),
    "landmines": "Already pay for Copilot Enterprise.",
    "compound_candidate": True,
    "requalified_contacts": [],
}

CONTACT = {
    "ref": "contact-001",
    "account_ref": "acct-100",
    "name": "Jane Smith",
    "title": "VP Engineering",
    "email": "jane@vercellike.com",
    "phone": None,
    "linkedin_url": "https://linkedin.com/in/janesmith",
}

CHANNEL_PLAN = ["email", "call", "linkedin"]

COPILOT_OBJECTION_THREAD = [
    {
        "id": "msg-001",
        "thread_ref": "thread-001",
        "sender": "jane@vercellike.com",
        "to": ["malay@cognition.ai"],
        "subject": "Re: Devin for VercelLike",
        "body": "We already have Copilot, not sure we need another tool.",
        "sent_at": "2026-06-09T10:00:00",
    },
]


def _make_job_input(
    brief=None,
    contact=None,
    channel_plan=None,
    thread_history=None,
    rejection_feedback=None,
):
    return {
        "brief": brief or HIRING_SURGE_BRIEF,
        "contact": contact or CONTACT,
        "channel_plan": channel_plan or CHANNEL_PLAN,
        "voice_profile": "Direct, concise, no fluff.",
        "value_props": "Devin: autonomous software engineer.",
        "thread_history": thread_history,
        "rejection_feedback": rejection_feedback,
    }


# ── Golden LLM Mock Responses ────────────────────────────────────────

_GOLDEN_1_PACK = {
    "email_variants": [
        {
            "angle": "signal-direct",
            "subject": "27 backend openings — congrats on the growth",
            "body": (
                "Saw the 27 backend openings — congrats on the growth. "
                "Usually that means seniors spend the next two quarters "
                "onboarding instead of shipping. Teams like Vercel are "
                "using Devin to keep velocity flat through hiring waves. "
                "Worth 15 minutes to see how?"
            ),
            "rationale": "Directly reference hiring surge signal; CTA is a 15-min demo.",
        },
        {
            "angle": "problem-led",
            "subject": "When every new hire slows the seniors down",
            "body": (
                "Growing 40% QoQ is exciting until your senior engineers "
                "spend more time onboarding than shipping. The backlog grows, "
                "deadlines slip, and morale dips. Devin handles the routine "
                "tickets so your seniors stay on the critical path. "
                "Quick 15-min walkthrough?"
            ),
            "rationale": "Lead with onboarding pain, position Devin as the fix.",
        },
        {
            "angle": "social-proof",
            "subject": "How [similar co] kept velocity flat during a hiring wave",
            "body": (
                "When a Series C infra company scaled eng 35% in one quarter, "
                "they gave Devin the grunt-work tickets. Result: seniors shipped "
                "the same cadence while juniors ramped 40% faster. Your 27 open "
                "reqs tell a similar story. Worth a 15-min look?"
            ),
            "rationale": "Social proof from similar company; mirrors their situation.",
        },
    ],
    "call_opener": (
        "Hi Jane, saw your team is hiring 27 backend engineers — "
        "congrats. Quick question: how are your seniors handling "
        "the onboarding load?"
    ),
    "voicemail": (
        "Hi Jane, this is Malay from Cognition. Saw the 27 backend openings "
        "and wanted to share how teams like yours keep velocity flat during "
        "hiring surges. I'll shoot you a quick email — worth a look."
    ),
    "linkedin_note": (
        "Hi Jane — saw VercelLike is hiring 27 backend engineers. Congrats! "
        "Teams in similar growth phases use Devin to keep seniors shipping "
        "while juniors ramp. Worth a quick chat?"
    ),
    "rationale": [
        "signal-direct: reference hiring surge, short CTA",
        "problem-led: onboarding pain → Devin solves",
        "social-proof: similar company proof point",
    ],
    "confidence": 0.88,
    "needs_human_because": None,
}
GOLDEN_1_RESPONSE = json.dumps(_GOLDEN_1_PACK)

_GOLDEN_2_PACK = {
    "email_variants": [
        {
            "angle": "signal-direct",
            "subject": "Re: Devin for VercelLike — Copilot vs. delegation",
            "body": (
                "Totally fair — Copilot is great for autocomplete. "
                "Devin is different: it takes a whole ticket, writes the code, "
                "tests it, and opens the PR. Side-by-side on one real ticket "
                "from your backlog would show the difference. "
                "15 minutes, your ticket?"
            ),
            "rationale": "Reframe Copilot objection: autocomplete vs. ticket delegation.",
        },
        {
            "angle": "problem-led",
            "subject": "Re: The real bottleneck isn't autocomplete",
            "body": (
                "Copilot speeds up typing, but the bottleneck is usually "
                "the tickets no one wants to pick up — migrations, boilerplate, "
                "test coverage. Devin handles those end-to-end. Pick one ticket "
                "from your backlog and I'll show you the difference live. 15 min?"
            ),
            "rationale": "Problem: undesirable tickets pile up; Devin clears the backlog.",
        },
        {
            "angle": "social-proof",
            "subject": "Re: What teams with Copilot add next",
            "body": (
                "Most teams we work with already have Copilot — and love it. "
                "They add Devin for the tasks Copilot can't do: full-ticket "
                "implementation, end-to-end. One infra team cut their backlog "
                "30% in the first month. Worth a 15-min side-by-side on your code?"
            ),
            "rationale": "Social proof: teams with Copilot still add Devin; complementary.",
        },
    ],
    "call_opener": (
        "Hi Jane, I saw your note about Copilot — totally fair. "
        "Quick thought: Copilot autocompletes, Devin takes a whole "
        "ticket. Worth seeing the difference live?"
    ),
    "voicemail": (
        "Hi Jane, Malay from Cognition. I saw your reply about Copilot. "
        "Totally get it. The difference is Devin takes whole tickets, "
        "not just code lines. I'll send a quick comparison."
    ),
    "linkedin_note": (
        "Hi Jane — saw your note about Copilot. Totally fair. "
        "Devin handles whole tickets end-to-end vs. autocomplete. "
        "Worth a quick side-by-side on one of your real tickets?"
    ),
    "rationale": [
        "signal-direct: reframe objection, CTA = side-by-side",
        "problem-led: ticket backlog pain, not autocomplete",
        "social-proof: Copilot + Devin = complementary, proof",
    ],
    "confidence": 0.82,
    "needs_human_because": None,
}
GOLDEN_2_RESPONSE = json.dumps(_GOLDEN_2_PACK)

# Variant with word limit violations for regeneration test
_OVER_LIMIT_PACK = {
    **_GOLDEN_1_PACK,
    "email_variants": [
        {
            **_GOLDEN_1_PACK["email_variants"][0],
            "body": " ".join(["word"] * 95),  # 95 words > 90
        },
        _GOLDEN_1_PACK["email_variants"][1],
        _GOLDEN_1_PACK["email_variants"][2],
    ],
}
OVER_LIMIT_RESPONSE = json.dumps(_OVER_LIMIT_PACK)


class MockLLMCallable:
    """Mock for AgentBase._call_llm that returns preset responses."""

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
        return resp, 150, 80


# ── Tests: CopyPack Schema ────────────────────────────────────────────

class TestCopyPackSchema:
    """test_copypack_schema — word/char limits enforced via regeneration
    not truncation; 3 distinct angles present (assert rationale labels)."""

    def test_valid_pack_from_hiring_surge(self):
        """Valid CopyPack: 3 variants, all angles, within limits."""
        agent = CopyAgent()
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input())

        assert result.success
        assert result.output is not None
        pack = CopyPack.model_validate({
            "confidence": result.output.confidence,
            "needs_human_because": result.output.needs_human_because,
            **result.output.data,
        })
        assert len(pack.email_variants) == 3
        angles = {ev.angle for ev in pack.email_variants}
        assert angles == MANDATED_ANGLES

        # Word/char limits
        for i, ev in enumerate(pack.email_variants):
            wc = len(ev.body.split())
            assert wc <= 90, f"variant {i} ({ev.angle}): {wc} words > 90"
        assert len(pack.call_opener.split()) <= 40
        assert len(pack.linkedin_note) <= 280

        # Rationale labels present
        assert len(pack.rationale) == 3
        for r in pack.rationale:
            assert len(r) > 0

    def test_regeneration_on_word_limit_violation(self):
        """Over-limit first attempt → regeneration with tighter constraints → pass."""
        agent = CopyAgent()
        mock_llm = MockLLMCallable([OVER_LIMIT_RESPONSE, GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input())

        assert result.success
        assert mock_llm.call_count == 2  # regenerated once
        # Second call should have the retry hint
        assert "_retry_hint" in mock_llm.calls[1][1] or "VIOLATED LIMITS" in mock_llm.calls[1][1]

    def test_distinct_angles_validated(self):
        """Pydantic rejects CopyPack with duplicate/missing angles."""
        with pytest.raises(Exception):
            CopyPack.model_validate({
                "email_variants": [
                    {"angle": "signal-direct", "subject": "s", "body": "b", "rationale": "r"},
                    {"angle": "signal-direct", "subject": "s", "body": "b", "rationale": "r"},
                    {"angle": "signal-direct", "subject": "s", "body": "b", "rationale": "r"},
                ],
                "call_opener": "test",
                "voicemail": "test",
                "linkedin_note": "test",
                "rationale": ["a", "b", "c"],
                "confidence": 0.5,
            })


# ── Tests: Gate Unbypassable ──────────────────────────────────────────

class TestGateUnbypassable:
    """test_gate_unbypassable — attempts to whitelist outreach_draft → boot failure."""

    def test_customer_facing_hardcoded_true(self):
        """CopyAgent.customer_facing is True at class level."""
        assert CopyAgent.customer_facing is True

    def test_subclass_override_false_raises(self):
        """Subclassing with customer_facing=False → boot failure."""

        class EvilCopyAgent(CopyAgent):
            customer_facing = False

        with pytest.raises(RuntimeError, match="customer_facing must be True"):
            EvilCopyAgent()

    def test_auto_approve_whitelist_excludes_outreach_draft(self):
        """outreach_draft is not in policy.yaml auto_approve_whitelist."""
        import yaml

        policy_path = Path(__file__).resolve().parent.parent / "app" / "policy" / "policy.yaml"
        with open(policy_path) as f:
            policy = yaml.safe_load(f)
        whitelist = policy.get("auto_approve_whitelist", [])
        assert "outreach_draft" not in whitelist
        assert "copy" not in whitelist

    def test_monkeypatching_class_attr_still_checked_at_boot(self):
        """Even runtime patching of customer_facing=False caught at __init__."""
        original = CopyAgent.customer_facing
        try:
            CopyAgent.customer_facing = False
            with pytest.raises(RuntimeError, match="customer_facing must be True"):
                CopyAgent()
        finally:
            CopyAgent.customer_facing = original


# ── Tests: Refuses Without Angle ──────────────────────────────────────

class TestRefusesWithoutAngle:
    """test_refuses_without_angle — Golden 3 behavior."""

    def test_missing_angle_key(self):
        """brief with no angle key → needs_human_because set, no copy produced."""
        agent = CopyAgent()
        brief_no_angle = {k: v for k, v in HIRING_SURGE_BRIEF.items() if k != "angle"}
        result = agent.run(_make_job_input(brief=brief_no_angle))

        assert result.success  # succeeds (refusal is not a failure)
        assert result.output is not None
        assert result.output.needs_human_because == "no angle — refusing to write generic spray"
        assert result.output.confidence == 0.0
        assert result.output.data == {}

    def test_empty_angle(self):
        """brief.angle = '' → same refusal."""
        agent = CopyAgent()
        brief_empty = {**HIRING_SURGE_BRIEF, "angle": ""}
        result = agent.run(_make_job_input(brief=brief_empty))

        assert result.output is not None
        assert result.output.needs_human_because == "no angle — refusing to write generic spray"

    def test_whitespace_angle(self):
        """brief.angle = '   ' → refusal."""
        agent = CopyAgent()
        brief_ws = {**HIRING_SURGE_BRIEF, "angle": "   "}
        result = agent.run(_make_job_input(brief=brief_ws))

        assert result.output is not None
        assert result.output.needs_human_because == "no angle — refusing to write generic spray"

    def test_none_brief(self):
        """brief = None → refusal."""
        agent = CopyAgent()
        agent.run(_make_job_input(brief=None))

        # brief defaults to {} inside _make_job_input, but if we pass None explicitly:
        result2 = agent.run({"brief": None, "contact": CONTACT, "channel_plan": CHANNEL_PLAN})
        assert result2.output is not None
        assert result2.output.needs_human_because == "no angle — refusing to write generic spray"


# ── Tests: Rejection Feedback in Prompt ───────────────────────────────

class TestRejectionFeedbackInPrompt:
    """test_rejection_feedback_in_prompt — seed 2 rejections; assert their text
    reaches the prompt assembly."""

    def test_feedback_injected(self):
        """Rejection feedback text appears in the user message sent to LLM."""
        agent = CopyAgent()
        rejections = [
            "Too aggressive CTA — soften the ask",
            "Subject line too clickbaity — be more direct",
        ]
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input(rejection_feedback=rejections))

        assert result.success
        # Check the user message that was sent to LLM
        _, user_msg = mock_llm.calls[0]
        assert "Too aggressive CTA" in user_msg
        assert "Subject line too clickbaity" in user_msg
        assert "Recent Review Queue Rejections" in user_msg

    def test_max_five_feedback_items(self):
        """Only last 5 rejection items included in the feedback section."""
        agent = CopyAgent()
        rejections = [f"feedback-{i}" for i in range(10)]
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            agent.run(_make_job_input(rejection_feedback=rejections))

        _, user_msg = mock_llm.calls[0]
        # Extract just the feedback section
        section_marker = "Recent Review Queue Rejections"
        assert section_marker in user_msg
        feedback_section = user_msg[user_msg.index(section_marker):]
        # Last 5 should be present in the feedback section
        assert "feedback-5" in feedback_section
        assert "feedback-9" in feedback_section
        # First 5 should NOT be in the feedback section bullets
        assert "- feedback-0" not in feedback_section
        assert "- feedback-4" not in feedback_section

    def test_empty_feedback_no_section(self):
        """No rejection feedback → no 'Recent Review Queue' section in prompt."""
        agent = CopyAgent()
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            agent.run(_make_job_input(rejection_feedback=[]))

        _, user_msg = mock_llm.calls[0]
        assert "Recent Review Queue" not in user_msg


# ── Tests: Draft Not Send ─────────────────────────────────────────────

class TestDraftNotSend:
    """test_draft_not_send — write-back calls create_draft; send path raises
    if reached during DRAFT_ONLY."""

    def test_draft_only_allows_create_draft(self):
        """DRAFT_ONLY scope check passes for create_draft."""
        DRAFT_ONLY.assert_draft_only("create_draft")  # should not raise

    def test_draft_only_blocks_send(self):
        """DRAFT_ONLY scope check raises for send."""
        with pytest.raises(RuntimeError, match="DRAFT_ONLY violation"):
            DRAFT_ONLY.assert_draft_only("send")

    @pytest.mark.asyncio
    async def test_writeback_calls_create_draft(self):
        """process_copy_writeback calls email_adapter.create_draft, not send."""
        mock_email = AsyncMock()
        mock_email.create_draft.return_value = "draft-001"
        mock_email.send.side_effect = RuntimeError("send should not be called")

        mock_crm = AsyncMock()
        mock_crm.log_activity.return_value = "act-001"

        result = AgentRunResult(
            output=AgentOutput(
                confidence=0.88,
                needs_human_because=None,
                data=_GOLDEN_1_PACK,
            ),
            success=True,
        )
        approval = {"selected_variant_idx": 0, "decided_by": "user"}

        wb = await process_copy_writeback(
            result,
            _make_job_input(),
            approval,
            email_adapter=mock_email,
            crm_adapter=mock_crm,
        )

        mock_email.create_draft.assert_called_once()
        mock_email.send.assert_not_called()
        assert wb["draft_id"] == "draft-001"
        assert wb["sequencer"] == "[CONNECT LATER]"

    @pytest.mark.asyncio
    async def test_writeback_logs_crm_activity(self):
        """process_copy_writeback logs CRM activity."""
        mock_email = AsyncMock()
        mock_email.create_draft.return_value = "draft-002"
        mock_crm = AsyncMock()
        mock_crm.log_activity.return_value = "act-002"

        result = AgentRunResult(
            output=AgentOutput(
                confidence=0.88,
                needs_human_because=None,
                data=_GOLDEN_1_PACK,
            ),
            success=True,
        )
        approval = {"selected_variant_idx": 0}

        wb = await process_copy_writeback(
            result,
            _make_job_input(),
            approval,
            email_adapter=mock_email,
            crm_adapter=mock_crm,
        )

        mock_crm.log_activity.assert_called_once()
        assert wb["crm_ref"] == "act-002"

    @pytest.mark.asyncio
    async def test_edit_diff_preserved_in_approval(self):
        """approval.edit_diff is carried through for feedback loop storage."""
        mock_email = AsyncMock()
        mock_email.create_draft.return_value = "draft-003"
        mock_crm = AsyncMock()
        mock_crm.log_activity.return_value = "act-003"

        result = AgentRunResult(
            output=AgentOutput(
                confidence=0.88,
                needs_human_because=None,
                data=_GOLDEN_1_PACK,
            ),
            success=True,
        )
        approval = {
            "selected_variant_idx": 0,
            "edit_diff": {"subject": {"old": "A", "new": "B"}},
        }

        await process_copy_writeback(
            result,
            _make_job_input(),
            approval,
            email_adapter=mock_email,
            crm_adapter=mock_crm,
        )
        # edit_diff is preserved in the approval dict (caller persists it)
        assert approval["edit_diff"] == {"subject": {"old": "A", "new": "B"}}


# ── Golden Tests ──────────────────────────────────────────────────────

class TestGoldenCopy:
    """Golden tests for AGENTS.md §2 examples."""

    def test_golden_1_hiring_surge(self):
        """Golden 1: hiring_surge brief → signal-direct variant references
        '27 backend openings', CTA present."""
        agent = CopyAgent()
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input())

        assert result.success
        pack = CopyPack.model_validate({
            "confidence": result.output.confidence,
            "needs_human_because": result.output.needs_human_because,
            **result.output.data,
        })

        # signal-direct variant references the signal evidence
        sd = [ev for ev in pack.email_variants if ev.angle == "signal-direct"][0]
        assert "27" in sd.body or "backend" in sd.body
        assert "15 min" in sd.body.lower() or "worth" in sd.body.lower()

        # All three mandated angles present with rationale
        for ev in pack.email_variants:
            assert ev.rationale, f"Missing rationale for {ev.angle}"

    def test_golden_2_copilot_objection_reframe(self):
        """Golden 2: reply-thread with Copilot objection → variant reframes
        to autocomplete vs. ticket delegation."""
        agent = CopyAgent()
        mock_llm = MockLLMCallable([GOLDEN_2_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input(thread_history=COPILOT_OBJECTION_THREAD))

        assert result.success
        pack = CopyPack.model_validate({
            "confidence": result.output.confidence,
            "needs_human_because": result.output.needs_human_because,
            **result.output.data,
        })

        # At least one variant addresses Copilot objection
        bodies = " ".join(ev.body for ev in pack.email_variants)
        assert "copilot" in bodies.lower() or "autocomplete" in bodies.lower()
        assert "ticket" in bodies.lower() or "delegation" in bodies.lower()

    def test_golden_3_missing_angle_refuses(self):
        """Golden 3: missing brief.angle → needs_human_because set, no copy."""
        agent = CopyAgent()
        brief_no_angle = {k: v for k, v in HIRING_SURGE_BRIEF.items() if k != "angle"}
        result = agent.run(_make_job_input(brief=brief_no_angle))

        assert result.success
        assert result.output.needs_human_because == "no angle — refusing to write generic spray"
        assert result.output.data == {}


# ── Tests: Build Helpers ──────────────────────────────────────────────

class TestBuildHelpers:
    def test_build_writeback_draft(self):
        draft = build_writeback_draft(
            _GOLDEN_1_PACK, 0, "jane@vercellike.com", "contact-001"
        )
        assert draft["to"] == ["jane@vercellike.com"]
        assert draft["subject"] == _GOLDEN_1_PACK["email_variants"][0]["subject"]

    def test_build_crm_activity(self):
        act = build_crm_activity(_GOLDEN_1_PACK, 0, "acct-100", "contact-001")
        assert act["account_ref"] == "acct-100"
        assert "signal-direct" in act["subject"]
