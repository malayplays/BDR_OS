"""Copy Agent — generates multi-channel outreach copy packs.

Spec: AGENTS.md §2
- Trigger: Approved/auto research_brief; sequence step due; signal-triggered touch
- Output: CopyPack (3 email variants ≤90 words w/ distinct angles, call_opener ≤40 words
  + voicemail, linkedin_note ≤280 chars, per-variant rationale)
- Approval: REQUIRED — customer-facing. No auto lane, ever, in v1.
- Write-back: Approved variant → EmailAdapter.create_draft (draft only);
  CRM activity logged; sequencer write-back stubbed [CONNECT LATER]
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, field_validator

from app.agents.base import AgentBase, AgentOutput, AgentRunResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Angles mandated by spec — every CopyPack must use all three
# ---------------------------------------------------------------------------
MANDATED_ANGLES = frozenset({"signal-direct", "problem-led", "social-proof"})

# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------


class EmailVariant(BaseModel):
    angle: str  # one of MANDATED_ANGLES
    subject: str
    body: str  # ≤90 words, 1 CTA, no images/links beyond 1
    rationale: str  # 1 line explaining variant strategy

    @field_validator("angle")
    @classmethod
    def validate_angle(cls, v: str) -> str:
        if v not in MANDATED_ANGLES:
            raise ValueError(
                f"angle must be one of {sorted(MANDATED_ANGLES)}, got '{v}'"
            )
        return v


class CopyPack(BaseModel):
    """CopyPack output — three email variants + call + linkedin."""

    email_variants: list[EmailVariant]  # exactly 3
    call_opener: str  # ≤40 words
    voicemail: str
    linkedin_note: str  # ≤280 chars
    rationale: list[str]  # 1 line per variant (top-level summary)
    confidence: float
    needs_human_because: str | None = None

    @field_validator("email_variants")
    @classmethod
    def exactly_three_variants(cls, v: list[EmailVariant]) -> list[EmailVariant]:
        if len(v) != 3:
            raise ValueError(f"Must have exactly 3 email variants, got {len(v)}")
        angles_present = {ev.angle for ev in v}
        if angles_present != MANDATED_ANGLES:
            missing = MANDATED_ANGLES - angles_present
            raise ValueError(f"Missing mandated angles: {sorted(missing)}")
        return v

    def word_count_email(self, idx: int) -> int:
        return len(self.email_variants[idx].body.split())

    def word_count_call_opener(self) -> int:
        return len(self.call_opener.split())


# ---------------------------------------------------------------------------
# Approval gate — hard-coded REQUIRED
# ---------------------------------------------------------------------------
class _DraftOnlyScope:
    """Sentinel — ensures CopyAgent write-back never calls send."""

    @staticmethod
    def assert_draft_only(method_name: str) -> None:
        if method_name != "create_draft":
            raise RuntimeError(
                f"DRAFT_ONLY violation: CopyAgent may only call "
                f"create_draft, not '{method_name}'. "
                f"customer_facing=True agents are draft-only in v1."
            )


DRAFT_ONLY = _DraftOnlyScope()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class CopyAgent(AgentBase):
    """Generates CopyPack from an approved research brief."""

    agent_name = "copy"

    # Hard-coded approval gate — no config can put this on the auto lane.
    # Mirrors dispatcher boot check: outreach_draft tagged customer_facing
    # always requires approval.
    customer_facing: bool = True

    def __init__(self) -> None:
        super().__init__()
        # Boot-time assertion: customer_facing MUST be True
        if not self.__class__.customer_facing:
            raise RuntimeError(
                f"CopyAgent boot failure: customer_facing must be True. "
                f"Got {self.__class__.customer_facing!r}. "
                f"Outreach drafts are ALWAYS approval-gated in v1."
            )

    def _system_prompt(self) -> str:
        return (
            "You are the Copy Agent for a BDR automation system.\n\n"
            "Your job: given an approved research brief, a contact, channel plan, "
            "and context, produce a CopyPack with multi-channel outreach copy.\n\n"
            "CRITICAL CONSTRAINTS:\n"
            "- email_variants: EXACTLY 3, one per angle:\n"
            "  1. signal-direct — directly reference the trigger signal\n"
            "  2. problem-led — lead with a pain point the signal implies\n"
            "  3. social-proof — lead with proof/example from similar companies\n"
            "- Each email body: ≤90 words, 1 CTA, no images, max 1 link\n"
            "- call_opener: ≤40 words + a separate voicemail line\n"
            "- linkedin_note: ≤280 characters total\n"
            "- rationale: 1 line per variant explaining the strategic angle\n"
            "- If the brief has no angle (empty/missing brief.angle), you MUST "
            "return needs_human_because='no angle — refusing to write generic "
            "spray' and set confidence to 0. Do NOT produce filler copy.\n"
            "- If thread_history contains a reply/objection, reframe your copy "
            "to address it directly. Never restart the pitch.\n\n"
            "Output ONLY valid JSON matching the CopyPack schema. "
            "No markdown, no explanation."
        )

    def _build_user_message(self, job_input: dict) -> str:
        parts = [json.dumps(job_input, indent=2, default=str)]
        # Inject rejection feedback into prompt context
        feedback = job_input.get("rejection_feedback") or []
        if feedback:
            parts.append(
                "\n\n## Recent Review Queue Rejections/Edits (learn from these):\n"
                + "\n".join(f"- {f}" for f in feedback[-5:])
            )
        return "\n".join(parts)

    def _output_schema(self) -> type[CopyPack]:
        return CopyPack

    def run(self, job_input: dict) -> AgentRunResult:
        """Run with Golden-3 refusal path and word/char limit enforcement."""
        # --- Golden 3: refuse if brief.angle is missing/empty ---
        brief = job_input.get("brief") or {}
        angle = brief.get("angle")
        if not angle or not str(angle).strip():
            return AgentRunResult(
                output=AgentOutput(
                    confidence=0.0,
                    needs_human_because="no angle — refusing to write generic spray",
                    data={},
                ),
                success=True,
            )

        result = super().run(job_input)
        if not result.success or result.output is None:
            return result

        # Validate & enforce limits via regeneration (not truncation)
        pack = CopyPack.model_validate({
            "confidence": result.output.confidence,
            "needs_human_because": result.output.needs_human_because,
            **result.output.data,
        })

        violations = self._check_limits(pack)
        if violations:
            # One regeneration attempt with tighter constraints
            tighter_input = {
                **job_input,
                "_retry_hint": (
                    f"PREVIOUS OUTPUT VIOLATED LIMITS: {'; '.join(violations)}. "
                    "Fix these. Do NOT truncate — rewrite to fit."
                ),
            }
            result = super().run(tighter_input)
            if not result.success or result.output is None:
                return result
            pack = CopyPack.model_validate({
                "confidence": result.output.confidence,
                "needs_human_because": result.output.needs_human_because,
                **result.output.data,
            })
            violations = self._check_limits(pack)
            if violations:
                result.success = False
                result.error = (
                    f"CopyPack limit violations after regeneration: "
                    f"{'; '.join(violations)}"
                )
                return result

        return result

    @staticmethod
    def _check_limits(pack: CopyPack) -> list[str]:
        """Return list of limit violations (empty = all good)."""
        issues: list[str] = []
        for i, ev in enumerate(pack.email_variants):
            wc = len(ev.body.split())
            if wc > 90:
                issues.append(f"email_variants[{i}] ({ev.angle}): {wc} words > 90")
        co_wc = len(pack.call_opener.split())
        if co_wc > 40:
            issues.append(f"call_opener: {co_wc} words > 40")
        if len(pack.linkedin_note) > 280:
            issues.append(f"linkedin_note: {len(pack.linkedin_note)} chars > 280")
        return issues


# ---------------------------------------------------------------------------
# Write-back pipeline
# ---------------------------------------------------------------------------
def build_writeback_draft(
    pack_data: dict,
    approved_variant_idx: int,
    contact_email: str,
    contact_ref: str | None,
    thread_ref: str | None = None,
) -> dict:
    """Build DraftEmail payload from approved variant."""
    variant = pack_data["email_variants"][approved_variant_idx]
    return {
        "to": [contact_email],
        "subject": variant["subject"],
        "body": variant["body"],
        "thread_ref": thread_ref,
    }


def build_crm_activity(
    pack_data: dict,
    approved_variant_idx: int,
    account_ref: str,
    contact_ref: str | None,
) -> dict:
    """Build CRM activity log payload."""
    variant = pack_data["email_variants"][approved_variant_idx]
    return {
        "account_ref": account_ref,
        "contact_ref": contact_ref,
        "activity_type": "outreach_draft",
        "subject": f"[BDR-OS] Copy draft — {variant['angle']}",
        "body": variant["body"],
    }


async def process_copy_writeback(
    result: AgentRunResult,
    job_input: dict,
    approval: dict,
    *,
    email_adapter: Any = None,
    crm_adapter: Any = None,
    policy_check: Any = None,
) -> dict:
    """Execute write-back for approved CopyPack.

    NEVER calls send — only create_draft. Enforced by DRAFT_ONLY scope check.
    Sequencer write-back is stubbed [CONNECT LATER].

    Parameters:
        result: AgentRunResult from CopyAgent.run()
        job_input: Original job input_payload
        approval: JobApproval dict (with selected_variant_idx, edit_diff, etc.)
        email_adapter: EmailAdapter instance
        crm_adapter: CRMAdapter instance
        policy_check: callable(WriteBackAction) -> Verdict
    """
    from app.adapters.interfaces.types import ActivityWrite, DraftEmail
    from app.policy.guardrails import WriteBackAction
    from app.policy.guardrails import check as default_check

    wb_result: dict[str, Any] = {"draft_id": None, "crm_ref": None, "sequencer": "[CONNECT LATER]"}

    if not result.success or result.output is None:
        return wb_result

    pack_data = result.output.data
    variant_idx = approval.get("selected_variant_idx", 0)
    contact = job_input.get("contact", {})
    account_ref = contact.get("account_ref", "")
    contact_email = contact.get("email", "")
    contact_ref = contact.get("ref")
    history = job_input.get("thread_history")
    thread_ref = (history or [{}])[0].get("thread_ref") if history else None

    # Store edit_diff on approval for feedback loop
    if approval.get("edit_diff"):
        # This will be persisted by the caller on the Job.approval field
        pass

    # --- Email draft (NEVER send) ---
    DRAFT_ONLY.assert_draft_only("create_draft")
    if email_adapter:
        draft_payload = build_writeback_draft(
            pack_data, variant_idx, contact_email, contact_ref, thread_ref
        )
        check_fn = policy_check or default_check
        action = WriteBackAction(
            action_type="create_draft",
            account_ref=account_ref,
            contact_ref=contact_ref,
            channel="email",
            is_customer_facing=True,
        )
        verdict = check_fn(action)
        if verdict.result.value in ("ALLOW", "REQUIRE_APPROVAL"):
            draft = DraftEmail(**draft_payload)
            wb_result["draft_id"] = await email_adapter.create_draft(verdict, draft)

    # --- CRM activity log ---
    if crm_adapter and account_ref:
        crm_payload = build_crm_activity(pack_data, variant_idx, account_ref, contact_ref)
        action = WriteBackAction(
            action_type="crm_note_log",
            account_ref=account_ref,
            is_customer_facing=False,
        )
        check_fn = policy_check or default_check
        verdict = check_fn(action)
        if verdict.result.value == "ALLOW":
            activity = ActivityWrite(**crm_payload)
            wb_result["crm_ref"] = await crm_adapter.log_activity(verdict, activity)

    # --- Sequencer write-back [CONNECT LATER] ---
    # wb_result["sequencer"] already set to "[CONNECT LATER]"

    return wb_result
