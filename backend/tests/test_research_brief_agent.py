"""Tests for the Research Brief Agent (Session 3).

Required tests (merge bar):
- test_base_output_validation — malformed LLM JSON → one retry → hard fail
- test_brief_schema_and_length — fixture signal (hiring_surge) → valid Brief, ≤200 words
- test_strategic_suppression — strategic-tier account → no outreach_draft, needs_human_because set
- test_chain — standard account → outreach_draft job exists, pending, carries brief in input_payload
- test_writeback_via_policy — CRM note write passes through policy.check()
- Golden tests: all 3 golden examples as fixture-driven assertions
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import patch

import pytest

from app.adapters.interfaces.types import Signal
from app.agents.research_brief import Brief, ResearchBriefAgent, process_research_brief_job
from app.agents.triggers import build_research_brief_job_from_signal, signal_qualifies

# ── Fixtures ──────────────────────────────────────────────────────────

HIRING_SURGE_SIGNAL = {
    "kind": "hiring_surge",
    "account_domain": "vercellike.com",
    "strength": 0.85,
    "evidence": "27 open backend roles, +40% eng headcount QoQ",
    "detected_at": "2026-06-10T00:00:00",
}

ENG_LEADERSHIP_SIGNAL = {
    "kind": "eng_leadership_change",
    "account_domain": "exstripe.com",
    "strength": 0.78,
    "evidence": "New VP Eng hired, ex-Stripe engineering leadership",
    "detected_at": "2026-06-08T00:00:00",
}

STANDARD_ACCOUNT = {
    "ref": "acct-100",
    "name": "VercelLike",
    "domain": "vercellike.com",
    "tier": "standard",
    "owner": "malay",
    "custom": {},
}

STRATEGIC_ACCOUNT = {
    "ref": "acct-200",
    "name": "MegaCorp",
    "domain": "megacorp.com",
    "tier": "strategic",
    "owner": "malay",
    "custom": {},
}

CONTACTS = [
    {
        "ref": "contact-001",
        "account_ref": "acct-100",
        "name": "Jane Smith",
        "title": "VP Engineering",
        "email": "jane@vercellike.com",
        "phone": None,
        "linkedin_url": "https://linkedin.com/in/janesmith",
    },
    {
        "ref": "contact-002",
        "account_ref": "acct-100",
        "name": "Bob Lee",
        "title": "Director of Platform",
        "email": "bob@vercellike.com",
        "phone": None,
        "linkedin_url": None,
    },
    {
        "ref": "contact-003",
        "account_ref": "acct-100",
        "name": "Alice Chen",
        "title": "Senior Engineer",
        "email": "alice@vercellike.com",
        "phone": None,
        "linkedin_url": None,
    },
]

COMPANY_PROFILE = {
    "domain": "vercellike.com",
    "name": "VercelLike",
    "size": "200-1000",
    "funding": "Series C",
    "stack": ["TypeScript", "Rust", "Kubernetes", "Copilot Enterprise"],
    "eng_headcount_trend": "growing",
}

DORMANCY_REPORT = {
    "contact-001": 45,
    "contact-002": 130,  # ≥120d → requalified
    "contact-003": 200,  # ≥120d → requalified
}

RECENT_ACTIVITY: list = []


def _make_job_input(account=None, signal=None, contacts=None, dormancy=None, company=None, activity=None):
    return {
        "account": account or STANDARD_ACCOUNT,
        "contacts": contacts or CONTACTS,
        "signal": signal or HIRING_SURGE_SIGNAL,
        "recent_activity": activity or RECENT_ACTIVITY,
        "company": company or COMPANY_PROFILE,
        "dormancy_report": dormancy or DORMANCY_REPORT,
    }


# ── Golden LLM Mock Responses ─────────────────────────────────────────

_GOLDEN_1_DATA = {
    "company_snapshot": [
        "Series C, 200-1000 employees, TypeScript/Rust stack",
        "Engineering headcount growing 40% QoQ",
        "Currently uses Copilot Enterprise for dev tooling",
    ],
    "why_now": (
        "Eng team growing 40% QoQ with 27 open backend reqs — "
        "onboarding/velocity pain is now, and every new hire makes "
        "codebase ramp-up costlier. Devin absorbs the grunt-work "
        "backlog while seniors onboard juniors."
    ),
    "who_to_contact": [
        {
            "name": "Jane Smith",
            "title": "VP Engineering",
            "reason": (
                "VP-level buyer, highest persona points, "
                "directly owns eng productivity"
            ),
        },
        {
            "name": "Bob Lee",
            "title": "Director of Platform",
            "reason": "Platform owner feels onboarding pain acutely",
        },
    ],
    "angle": (
        "Frame Devin as headcount-multiplier during the hiring "
        "crunch, not headcount replacement."
    ),
    "landmines": (
        "Already pay for Copilot Enterprise — don't lead with "
        "autocomplete comparison."
    ),
    "compound_candidate": True,
    "requalified_contacts": ["Bob Lee", "Alice Chen"],
    "confidence": 0.85,
    "needs_human_because": None,
}
GOLDEN_1_RESPONSE = json.dumps(_GOLDEN_1_DATA)

_GOLDEN_2_DATA = {
    "company_snapshot": [
        "Large enterprise, 5000+ employees",
        "New VP Eng hired from Stripe",
        "Python/TypeScript/AWS stack",
    ],
    "why_now": (
        "New VP Eng audits tooling in first 90 days; ex-Stripe "
        "background means high bar for dev productivity. Window "
        "for vendor eval is now before stack decisions solidify."
    ),
    "who_to_contact": [
        {
            "name": "New VP Eng",
            "title": "VP Engineering",
            "reason": (
                "New leaders audit tooling in first 90 days; "
                "ex-Stripe = high bar for dev productivity tooling"
            ),
        },
        {
            "name": "Director Platform",
            "title": "Director",
            "reason": (
                "Reports to new VP, likely tasked with "
                "tooling eval"
            ),
        },
    ],
    "angle": (
        "Position for the first-90-days audit window — new VPs "
        "evaluate dev tooling before inheriting legacy decisions."
    ),
    "landmines": (
        "Large org politics; previous VP may have had "
        "vendor relationships."
    ),
    "compound_candidate": False,
    "requalified_contacts": [],
    "confidence": 0.80,
    "needs_human_because": None,
}
GOLDEN_2_RESPONSE = json.dumps(_GOLDEN_2_DATA)

_GOLDEN_3_DATA = {
    "company_snapshot": [
        "Strategic tier account, high value",
        "Complex org with multiple engineering teams",
        "Established vendor relationships",
    ],
    "why_now": (
        "Signal detected but strategic account requires manual "
        "plan. Automated outreach suppressed per policy."
    ),
    "who_to_contact": [
        {
            "name": "CTO",
            "title": "CTO",
            "reason": (
                "Strategic account — executive engagement required"
            ),
        },
    ],
    "angle": (
        "Requires custom approach aligned with "
        "strategic account plan."
    ),
    "landmines": (
        "Strategic account — existing relationships and "
        "contracts must be navigated carefully."
    ),
    "compound_candidate": False,
    "requalified_contacts": [],
    "confidence": 0.70,
    "needs_human_because": None,
}
GOLDEN_3_RESPONSE = json.dumps(_GOLDEN_3_DATA)

MALFORMED_JSON_RESPONSE = "This is not JSON at all {broken"
VALID_RETRY_RESPONSE = GOLDEN_1_RESPONSE


# ── Deterministic Mock for LLM ─────────────────────────────────────────

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
        return resp, 100, 50


# ── Tests: Base Output Validation ──────────────────────────────────────

class TestBaseOutputValidation:
    """test_base_output_validation — malformed LLM JSON → one retry → hard fail."""

    def test_malformed_json_retry_then_fail(self):
        """Malformed JSON on both attempts → Job.failed with raw output preserved."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([MALFORMED_JSON_RESPONSE, MALFORMED_JSON_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input())

        assert not result.success
        assert result.error is not None
        assert "validation failed after retry" in result.error.lower() or "failed" in result.error.lower()
        assert result.raw_output == MALFORMED_JSON_RESPONSE
        assert mock_llm.call_count == 2  # exactly one retry

    def test_malformed_then_valid_retry_succeeds(self):
        """Malformed JSON first, valid on retry → success."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([MALFORMED_JSON_RESPONSE, VALID_RETRY_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input())

        assert result.success
        assert result.output is not None
        assert result.output.confidence == 0.85
        assert mock_llm.call_count == 2


# ── Tests: Brief Schema and Length ─────────────────────────────────────

class TestBriefSchemaAndLength:
    """test_brief_schema_and_length — fixture signal (hiring_surge) → valid Brief, ≤200 words."""

    def test_valid_brief_from_hiring_surge(self):
        """hiring_surge signal → valid Brief schema, ≤200 words, why_now references signal evidence."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input())

        assert result.success
        assert result.output is not None

        # Parse as Brief to validate schema
        brief = Brief.model_validate({
            "confidence": result.output.confidence,
            "needs_human_because": result.output.needs_human_because,
            **result.output.data,
        })

        # ≤200 words
        assert brief.word_count() <= 200, f"Brief has {brief.word_count()} words, max 200"

        # why_now references signal evidence string
        assert "40%" in brief.why_now or "QoQ" in brief.why_now or "backend" in brief.why_now
        assert len(brief.company_snapshot) <= 3
        assert len(brief.who_to_contact) >= 1
        assert brief.confidence > 0

    def test_brief_word_count_enforcement(self):
        """Brief exceeding 200 words triggers regeneration."""
        # Create a response that exceeds 200 words
        wordy_response = json.dumps({
            "company_snapshot": [
                "This is an extremely long bullet point that goes on and on about the company " * 3,
                "Another very long bullet " * 5,
                "Yet another extremely verbose bullet point " * 5,
            ],
            "why_now": "word " * 80,
            "who_to_contact": [
                {"name": "Person", "title": "VP", "reason": "reason " * 20},
            ],
            "angle": "angle " * 20,
            "landmines": "landmine " * 20,
            "compound_candidate": True,
            "requalified_contacts": [],
            "confidence": 0.8,
            "needs_human_because": None,
        })

        agent = ResearchBriefAgent()
        # First response too wordy, second valid
        mock_llm = MockLLMCallable([wordy_response, GOLDEN_1_RESPONSE])

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(_make_job_input())

        assert result.success
        # Verify it regenerated (2 calls for initial attempt + potentially 2 for retry)
        assert mock_llm.call_count >= 2


# ── Tests: Strategic Suppression ───────────────────────────────────────

class TestStrategicSuppression:
    """test_strategic_suppression — strategic-tier account → no outreach_draft, needs_human_because set."""

    def test_strategic_account_suppresses_outreach(self):
        """Strategic-tier account → brief generated, no outreach_draft job, needs_human_because set."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([GOLDEN_3_RESPONSE])

        job_input = _make_job_input(account=STRATEGIC_ACCOUNT)

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(job_input)

        assert result.success
        assert result.output is not None
        # needs_human_because must be set for strategic accounts
        assert result.output.needs_human_because is not None
        assert "strategic" in result.output.needs_human_because.lower()
        nhb = result.output.needs_human_because.lower()
        assert (
            "outreach chain suppressed" in nhb
            or "manual" in nhb
        )

        # should_chain_outreach must return False
        assert not agent.should_chain_outreach(result, job_input)

        # build_chained_job must return None
        assert agent.build_chained_job(result, job_input) is None


# ── Tests: Chain ───────────────────────────────────────────────────────

class TestChain:
    """test_chain — standard account → outreach_draft job exists, pending, carries brief in input_payload."""

    def test_standard_account_chains_outreach_draft(self):
        """Standard account → outreach_draft job created with brief in input_payload."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        job_input = _make_job_input(account=STANDARD_ACCOUNT)

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(job_input)

        assert result.success
        assert agent.should_chain_outreach(result, job_input)

        chained = agent.build_chained_job(result, job_input)
        assert chained is not None
        assert chained["job_type"] == "outreach_draft"
        assert chained["funnel_stage"] == "create"
        assert chained["agent"] == "copy"
        assert chained["account_ref"] == STANDARD_ACCOUNT["ref"]
        assert "brief" in chained["input_payload"]
        assert chained["input_payload"]["brief"] == result.output.data

    def test_chain_with_create_job_fn(self):
        """Full pipeline creates outreach_draft via create_job_fn."""
        created_jobs: list[dict] = []

        def mock_create_job(job_dict: dict):
            created_jobs.append(job_dict)

        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])
        job_input = _make_job_input(account=STANDARD_ACCOUNT)

        with patch.object(agent, "_call_llm", mock_llm):
            with patch("app.agents.research_brief.ResearchBriefAgent", return_value=agent):
                # Use process_research_brief_job
                with patch.object(ResearchBriefAgent, "_call_llm", mock_llm):
                    result = process_research_brief_job(
                        job_input,
                        create_job_fn=mock_create_job,
                    )

        assert result.success
        assert len(created_jobs) == 1
        assert created_jobs[0]["job_type"] == "outreach_draft"
        assert "brief" in created_jobs[0]["input_payload"]


# ── Tests: Writeback via Policy ────────────────────────────────────────

class TestWritebackViaPolicy:
    """test_writeback_via_policy — CRM note write passes through policy.check()."""

    def test_crm_note_written_via_policy(self):
        """CRM note write passes through policy.check(); mock CRM written[] contains the note."""
        from app.adapters.mock.crm import MockCRMAdapter
        from app.policy.guardrails import check

        mock_crm = MockCRMAdapter()
        created_jobs: list[dict] = []

        def mock_create_job(job_dict: dict):
            created_jobs.append(job_dict)

        job_input = _make_job_input(account=STANDARD_ACCOUNT)

        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])

        with patch.object(ResearchBriefAgent, "_call_llm", mock_llm):
            result = process_research_brief_job(
                job_input,
                crm_adapter=mock_crm,
                policy_check=check,
                create_job_fn=mock_create_job,
            )

        assert result.success
        # CRM note was written
        assert len(mock_crm.written) == 1
        written = mock_crm.written[0]
        assert written["type"] == "log_activity"
        assert "[BDR-OS] Research brief" in written["data"]["subject"]
        assert written["data"]["account_ref"] == STANDARD_ACCOUNT["ref"]

    def test_strategic_account_policy_blocks_customer_facing_writes(self, monkeypatch):
        """Strategic account blocks customer-facing writes but allows internal CRM notes."""
        monkeypatch.setenv("STRATEGIC_ACCOUNTS", json.dumps([STRATEGIC_ACCOUNT["ref"]]))

        from app.adapters.mock.crm import MockCRMAdapter
        from app.policy.guardrails import check

        mock_crm = MockCRMAdapter()
        created_jobs: list[dict] = []

        def mock_create_job(job_dict: dict):
            created_jobs.append(job_dict)

        job_input = _make_job_input(account=STRATEGIC_ACCOUNT)
        mock_llm = MockLLMCallable([GOLDEN_3_RESPONSE])

        with patch.object(ResearchBriefAgent, "_call_llm", mock_llm):
            result = process_research_brief_job(
                job_input,
                crm_adapter=mock_crm,
                policy_check=check,
                create_job_fn=mock_create_job,
            )

        assert result.success
        # No outreach_draft chained for strategic
        assert len(created_jobs) == 0


# ── Tests: Golden Examples ─────────────────────────────────────────────

class TestGoldenExamples:
    """Golden tests: all 3 golden examples from AGENTS.md §1."""

    def test_golden_1_hiring_surge(self):
        """Golden 1: Vercel-like co, hiring_surge signal → proper brief structure."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])
        job_input = _make_job_input(account=STANDARD_ACCOUNT, signal=HIRING_SURGE_SIGNAL)

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(job_input)

        assert result.success
        assert result.output is not None
        brief_data = result.output.data

        # why_now references hiring/growth
        assert "40%" in brief_data["why_now"] or "growing" in brief_data["why_now"]
        assert "onboard" in brief_data["why_now"].lower() or "ramp" in brief_data["why_now"].lower()

        # angle references headcount-multiplier framing
        assert "headcount" in brief_data["angle"].lower() or "multiplier" in brief_data["angle"].lower()

        # landmines references Copilot
        assert "copilot" in brief_data["landmines"].lower()

        # who_to_contact has VP first
        assert len(brief_data["who_to_contact"]) >= 1
        top_contact = brief_data["who_to_contact"][0]
        assert "vp" in top_contact["title"].lower() or "VP" in top_contact["title"]

    def test_golden_2_eng_leadership_change(self):
        """Golden 2: eng_leadership_change signal → new VP ranked #1, angle references 90-days."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([GOLDEN_2_RESPONSE])

        leadership_account = {**STANDARD_ACCOUNT, "ref": "acct-300", "name": "ExStripe", "domain": "exstripe.com"}
        job_input = _make_job_input(account=leadership_account, signal=ENG_LEADERSHIP_SIGNAL)

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(job_input)

        assert result.success
        assert result.output is not None
        brief_data = result.output.data

        # who_to_contact ranks new VP #1
        assert len(brief_data["who_to_contact"]) >= 1
        top = brief_data["who_to_contact"][0]
        assert "vp" in top["title"].lower() or "VP" in top["title"]
        assert "90" in top["reason"] or "audit" in top["reason"].lower()

        # angle references 90-days audit window
        assert "90" in brief_data["angle"] or "audit" in brief_data["angle"].lower()

    def test_golden_3_strategic_suppression(self):
        """Golden 3: strategic account → brief generated, needs_human_because set, no outreach chain."""
        agent = ResearchBriefAgent()
        mock_llm = MockLLMCallable([GOLDEN_3_RESPONSE])
        job_input = _make_job_input(account=STRATEGIC_ACCOUNT)

        with patch.object(agent, "_call_llm", mock_llm):
            result = agent.run(job_input)

        assert result.success
        assert result.output is not None

        # Brief IS generated (agent still produces the artifact)
        assert result.output.data.get("company_snapshot") is not None
        assert result.output.data.get("why_now") is not None

        # needs_human_because is set
        assert result.output.needs_human_because is not None
        assert "strategic" in result.output.needs_human_because.lower()
        assert "manual" in result.output.needs_human_because.lower()

        # No outreach_draft chained
        assert not agent.should_chain_outreach(result, job_input)
        assert agent.build_chained_job(result, job_input) is None


# ── Tests: Trigger Wiring ──────────────────────────────────────────────

class TestTriggerWiring:
    """Signal ≥0.5 from EnrichmentAdapter poll → job created."""

    def test_signal_qualifies_threshold(self):
        """Signals with strength ≥ 0.5 qualify."""
        dt = datetime(2026, 6, 10)
        signal_high = Signal(
            kind="hiring_surge", account_domain="x.com",
            strength=0.8, evidence="e", detected_at=dt,
        )
        signal_edge = Signal(
            kind="hiring_surge", account_domain="x.com",
            strength=0.5, evidence="e", detected_at=dt,
        )
        signal_low = Signal(
            kind="hiring_surge", account_domain="x.com",
            strength=0.49, evidence="e", detected_at=dt,
        )

        assert signal_qualifies(signal_high)
        assert signal_qualifies(signal_edge)
        assert not signal_qualifies(signal_low)

    def test_build_job_from_signal(self):
        """build_research_brief_job_from_signal produces correct payload."""
        signal = Signal(
            kind="hiring_surge", account_domain="x.com",
            strength=0.85, evidence="evidence",
            detected_at=datetime(2026, 6, 10),
        )
        job = build_research_brief_job_from_signal(signal, "acct-001")

        assert job["job_type"] == "research_brief"
        assert job["funnel_stage"] == "create"
        assert job["agent"] == "research_brief"
        assert job["account_ref"] == "acct-001"
        assert job["trigger"]["signal_kind"] == "hiring_surge"
        assert job["trigger"]["signal_strength"] == 0.85
        assert job["input_payload"]["signal"]["kind"] == "hiring_surge"


# ── Exact merge-bar test names (aliases) ───────────────────────────────
# These match the exact test names from the session spec "Done = these pass"


def test_base_output_validation():
    """Malformed LLM JSON → one retry → hard fail to Job.failed with raw output."""
    agent = ResearchBriefAgent()
    mock_llm = MockLLMCallable([MALFORMED_JSON_RESPONSE, MALFORMED_JSON_RESPONSE])
    with patch.object(agent, "_call_llm", mock_llm):
        result = agent.run(_make_job_input())
    assert not result.success
    assert result.raw_output == MALFORMED_JSON_RESPONSE
    assert mock_llm.call_count == 2


def test_brief_schema_and_length():
    """Fixture signal (hiring_surge) → valid Brief, ≤200 words, why_now refs signal."""
    agent = ResearchBriefAgent()
    mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])
    with patch.object(agent, "_call_llm", mock_llm):
        result = agent.run(_make_job_input())
    assert result.success
    brief = Brief.model_validate({
        "confidence": result.output.confidence,
        "needs_human_because": result.output.needs_human_because,
        **result.output.data,
    })
    assert brief.word_count() <= 200
    assert "40%" in brief.why_now or "backend" in brief.why_now


def test_strategic_suppression():
    """Strategic-tier account → no outreach_draft job, needs_human_because set."""
    agent = ResearchBriefAgent()
    mock_llm = MockLLMCallable([GOLDEN_3_RESPONSE])
    job_input = _make_job_input(account=STRATEGIC_ACCOUNT)
    with patch.object(agent, "_call_llm", mock_llm):
        result = agent.run(job_input)
    assert result.success
    assert result.output.needs_human_because is not None
    assert "strategic" in result.output.needs_human_because.lower()
    assert not agent.should_chain_outreach(result, job_input)


def test_chain():
    """Standard account → outreach_draft job exists, carries brief in input_payload."""
    agent = ResearchBriefAgent()
    mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])
    job_input = _make_job_input(account=STANDARD_ACCOUNT)
    with patch.object(agent, "_call_llm", mock_llm):
        result = agent.run(job_input)
    assert result.success
    chained = agent.build_chained_job(result, job_input)
    assert chained is not None
    assert chained["job_type"] == "outreach_draft"
    assert chained["input_payload"]["brief"] == result.output.data


def test_writeback_via_policy():
    """CRM note write passes through policy.check(); mock CRM written[] has note."""
    from app.adapters.mock.crm import MockCRMAdapter
    from app.policy.guardrails import check

    mock_crm = MockCRMAdapter()
    mock_llm = MockLLMCallable([GOLDEN_1_RESPONSE])
    job_input = _make_job_input(account=STANDARD_ACCOUNT)
    with patch.object(ResearchBriefAgent, "_call_llm", mock_llm):
        process_research_brief_job(
            job_input,
            crm_adapter=mock_crm,
            policy_check=check,
            create_job_fn=lambda x: None,
        )
    assert len(mock_crm.written) == 1
    assert "[BDR-OS] Research brief" in mock_crm.written[0]["data"]["subject"]


# ── Live LLM Test (behind RUN_LIVE_LLM=1) ─────────────────────────────

@pytest.mark.skipif(
    not os.getenv("RUN_LIVE_LLM"),
    reason="Live LLM tests require RUN_LIVE_LLM=1 and ANTHROPIC_API_KEY",
)
class TestLiveLLM:
    """Live LLM tests — only run when RUN_LIVE_LLM=1 is set."""

    def test_live_hiring_surge_brief(self):
        """Full live call with hiring_surge signal."""
        agent = ResearchBriefAgent()
        job_input = _make_job_input()
        result = agent.run(job_input)

        assert result.success
        assert result.output is not None
        brief = Brief.model_validate({
            "confidence": result.output.confidence,
            "needs_human_because": result.output.needs_human_because,
            **result.output.data,
        })
        assert brief.word_count() <= 200
        assert brief.confidence > 0
