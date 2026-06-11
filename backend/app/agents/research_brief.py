"""Research Brief Agent — generates research briefs from signals.

Spec: AGENTS.md §1
- Trigger: New signal from EnrichmentAdapter ≥0.5 strength
- Output: Brief ≤200 words
- Approval: Auto-approved (internal artifact)
- Write-back: CRM note on account + chain outreach_draft job
- Strategic: brief generated, outreach chain suppressed, needs_human_because set
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, field_validator

from app.agents.base import AgentBase, AgentRunResult


class ContactRanking(BaseModel):
    name: str
    title: str
    reason: str


class Brief(BaseModel):
    """Research Brief output schema — ≤200 words total."""

    company_snapshot: list[str]  # 3 bullets
    why_now: str  # signal-anchored, 2 sentences
    who_to_contact: list[ContactRanking]  # ranked 1-3
    angle: str  # 1 sentence connecting signal → Devin/Windsurf value
    landmines: str  # competitors in stack, bad press, existing relationship
    compound_candidate: bool  # net-new multi-thread potential
    requalified_contacts: list[str]  # ≥120d dormant contacts
    confidence: float
    needs_human_because: str | None = None

    @field_validator("company_snapshot")
    @classmethod
    def validate_snapshot_bullets(cls, v: list[str]) -> list[str]:
        if len(v) > 3:
            v = v[:3]
        return v

    def word_count(self) -> int:
        """Count total words in all text fields."""
        text_parts = []
        text_parts.extend(self.company_snapshot)
        text_parts.append(self.why_now)
        for c in self.who_to_contact:
            text_parts.append(f"{c.name} {c.title} {c.reason}")
        text_parts.append(self.angle)
        text_parts.append(self.landmines)
        text_parts.extend(self.requalified_contacts)
        full_text = " ".join(text_parts)
        return len(full_text.split())


class ResearchBriefAgent(AgentBase):
    """Generates research briefs from enrichment signals."""

    agent_name = "research_brief"

    def _system_prompt(self) -> str:
        return (
            "You are a Research Brief agent for a BDR automation system.\n\n"
            "Your job: given an account, contacts, a signal, and company context, "
            "produce a concise research brief for BDR prioritization.\n\n"
            "CRITICAL CONSTRAINTS:\n"
            "- Total output ≤200 words (all text fields combined). Be concise.\n"
            "- company_snapshot: exactly 3 bullets, each ≤15 words\n"
            "- why_now: exactly 2 sentences, signal-anchored\n"
            "- who_to_contact: rank 1-3 by persona points × reachability; "
            "VP+ first, never down-market\n"
            "- angle: 1 sentence connecting signal → Devin/Windsurf value\n"
            "- landmines: competitors in stack, bad press, existing relationship\n"
            "- compound_candidate: true if net-new multi-thread potential\n"
            "- requalified_contacts: names with ≥120 days dormant\n\n"
            "For strategic-tier accounts: generate the brief, but set "
            "needs_human_because to 'strategic account — outreach chain "
            "suppressed, manual plan required'\n\n"
            "Output ONLY valid JSON matching the Brief schema. "
            "No markdown, no explanation."
        )

    def _build_user_message(self, job_input: dict) -> str:
        return json.dumps(job_input, indent=2, default=str)

    def _output_schema(self) -> type[Brief]:
        return Brief

    def run(self, job_input: dict) -> AgentRunResult:
        """Run with ≤200-word enforcement: regenerate once on overflow."""
        result = super().run(job_input)

        if not result.success or result.output is None:
            return result

        # Validate word count — regeneration on overflow
        brief = Brief.model_validate({
            "confidence": result.output.confidence,
            "needs_human_because": result.output.needs_human_because,
            **result.output.data,
        })
        if brief.word_count() > 200:
            # One regeneration attempt with tighter constraint
            tighter_input = {
                **job_input,
                "_retry_hint": (
                    "PREVIOUS OUTPUT EXCEEDED 200 WORDS. "
                    "Be more concise. Max 200 words total."
                ),
            }
            result = super().run(tighter_input)
            if not result.success or result.output is None:
                return result
            brief = Brief.model_validate({
                "confidence": result.output.confidence,
                "needs_human_because": result.output.needs_human_because,
                **result.output.data,
            })
            if brief.word_count() > 200:
                result.success = False
                wc = brief.word_count()
                result.error = (
                    f"Brief exceeds 200 words ({wc} words) "
                    f"after regeneration"
                )
                return result

        # Strategic account suppression
        account = job_input.get("account", {})
        suppression_msg = (
            "strategic account — outreach chain suppressed, "
            "manual plan required"
        )
        if account.get("tier") == "strategic":
            result.output.needs_human_because = suppression_msg
            result.output.data["needs_human_because"] = suppression_msg

        return result

    def build_writeback(self, result: AgentRunResult, account_ref: str) -> dict:
        """Build the CRM note write-back payload."""
        if not result.success or result.output is None:
            return {}
        brief_text = json.dumps(result.output.data, indent=2)
        return {
            "account_ref": account_ref,
            "activity_type": "note",
            "subject": "[BDR-OS] Research brief",
            "body": brief_text,
        }

    def should_chain_outreach(self, result: AgentRunResult, job_input: dict) -> bool:
        """Determine if outreach_draft job should be chained."""
        if not result.success or result.output is None:
            return False
        # Strategic accounts: suppress outreach chain
        account = job_input.get("account", {})
        if account.get("tier") == "strategic":
            return False
        return True

    def build_chained_job(self, result: AgentRunResult, job_input: dict) -> dict | None:
        """Build the chained outreach_draft job input, or None if suppressed."""
        if not self.should_chain_outreach(result, job_input):
            return None
        return {
            "job_type": "outreach_draft",
            "funnel_stage": "create",
            "agent": "copy",
            "account_ref": job_input.get("account", {}).get("ref"),
            "input_payload": {
                "brief": result.output.data if result.output else {},
                "account": job_input.get("account"),
                "contacts": job_input.get("contacts"),
                "signal": job_input.get("signal"),
            },
        }


def process_research_brief_job(
    job_input: dict,
    *,
    crm_adapter: Any = None,
    policy_check: Any = None,
    create_job_fn: Any = None,
) -> AgentRunResult:
    """Full pipeline: run agent → writeback → chain.

    Parameters:
        job_input: The job's input_payload
        crm_adapter: CRMAdapter instance for writeback
        policy_check: callable(WriteBackAction) -> Verdict
        create_job_fn: callable(job_dict) -> Job for chaining
    """
    from app.adapters.interfaces.types import ActivityWrite
    from app.policy.guardrails import WriteBackAction
    from app.policy.guardrails import check as default_check

    agent = ResearchBriefAgent()
    result = agent.run(job_input)

    if not result.success or result.output is None:
        return result

    # Write-back: CRM note (policy-gated)
    account_ref = job_input.get("account", {}).get("ref", "")
    if crm_adapter and account_ref:
        writeback = agent.build_writeback(result, account_ref)
        action = WriteBackAction(
            action_type="crm_note_log",
            account_ref=account_ref,
            is_customer_facing=False,
        )
        check_fn = policy_check or default_check
        verdict = check_fn(action)
        if verdict.result.value == "ALLOW":
            import asyncio

            activity = ActivityWrite(**writeback)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                # Already in an async context — schedule as task
                loop.create_task(crm_adapter.log_activity(verdict, activity))
            else:
                asyncio.run(crm_adapter.log_activity(verdict, activity))

    # Chain outreach_draft job
    if create_job_fn and agent.should_chain_outreach(result, job_input):
        chained = agent.build_chained_job(result, job_input)
        if chained:
            create_job_fn(chained)

    return result
