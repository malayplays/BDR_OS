"""CRM Scribe Agent — transcript → structured CRM output.

Spec: AGENTS.md §8
- Trigger: New transcript from CallRecordingAdapter; manual voice-note
- Output: summary (5 bullets), sql_checklist (S1 bar), three_whys,
          next_steps [{action, owner, due}], crm_fields_patch,
          provenance_note
- Approval: Light gate — auto-approve summary/notes/provenance;
            next_steps + field patches REQUIRE approval
- Write-back: Salesforce activity note (auto) + tasks for approved
              next_steps (gated)
- Stage: scribe NEVER advances stage; may only propose s1_candidate: true
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, field_validator

from app.agents.base import AgentBase, AgentOutput, AgentRunResult

# ── CRM-field whitelist (non-whitelisted fields → REQUIRE_APPROVAL) ──

CRM_FIELD_WHITELIST: frozenset[str] = frozenset({
    "last_call_date",
    "last_call_summary",
    "call_count",
    "next_step",
    "next_step_date",
    "notes",
    "qualification_status",
    "s1_candidate",
})


# ── Output schema ─────────────────────────────────────────────────────


class NextStep(BaseModel):
    action: str
    owner: str
    due: str  # ISO date string


class SQLChecklist(BaseModel):
    """S1 qualification bar from COMP_MODEL.md §4."""

    icp_fit: bool = False
    relevant_title: bool = False
    expressed_pain: bool = False
    confirmed_need: bool = False
    next_steps_agreed: bool = False
    eval_timeline_6mo: bool = False
    facts_verified: bool = False


class ThreeWhys(BaseModel):
    anything: str | None = None
    now: str | None = None
    windsurf_devin: str | None = None


class ScribeOutput(BaseModel):
    """CRM Scribe output schema — AGENTS.md §8."""

    summary: list[str]  # 5 bullets
    sql_checklist: SQLChecklist
    three_whys: ThreeWhys
    next_steps: list[NextStep]
    crm_fields_patch: dict[str, Any]
    provenance_note: str
    s1_candidate: bool = False
    confidence: float
    needs_human_because: str | None = None

    @field_validator("summary")
    @classmethod
    def validate_summary_bullets(cls, v: list[str]) -> list[str]:
        if len(v) > 5:
            v = v[:5]
        return v


# ── Agent ──────────────────────────────────────────────────────────────


class CRMScribeAgent(AgentBase):
    """Processes call transcripts into structured CRM artifacts."""

    agent_name = "crm_scribe"

    def _system_prompt(self) -> str:
        return (
            "You are a CRM Scribe agent for a BDR automation system.\n\n"
            "Your job: given a call transcript, meeting context, account, "
            "and contact info, produce structured CRM output.\n\n"
            "OUTPUT SCHEMA (JSON only, no markdown):\n"
            "- summary: exactly 5 bullet strings summarizing the call\n"
            "- sql_checklist: {icp_fit, relevant_title, expressed_pain, "
            "confirmed_need, next_steps_agreed, eval_timeline_6mo, "
            "facts_verified} — each bool, the S1 qualification bar\n"
            "- three_whys: {anything, now, windsurf_devin} — captured "
            "from prospect's own words where possible\n"
            "- next_steps: [{action, owner, due}] — concrete actions\n"
            "- crm_fields_patch: key-value pairs for CRM field updates\n"
            "- provenance_note: outbound touch evidence for clawback log\n"
            "- s1_candidate: true ONLY if sql_checklist has sufficient "
            "trues (≥6/7) — this is a PROPOSAL for human review, NOT a "
            "stage advancement. You NEVER set s1_reached, s2_reached, "
            "or ad_accepted.\n"
            "- confidence: 0–1\n"
            "- needs_human_because: string or null\n\n"
            "CRITICAL RULES:\n"
            "1. NEVER emit any stage-advancement events (s1_reached, "
            "s2_reached, ad_accepted). Those come ONLY from CRM sync.\n"
            "2. s1_candidate is a proposal flag, not an event.\n"
            "3. Use prospect's actual words in three_whys.\n"
            "4. If eval_timeline is unconfirmed, add a next_step to "
            "confirm it.\n"
            "5. For objection-heavy calls, include an objection "
            "follow-up next_step.\n"
            "6. For no-show/reschedule calls, capture the new date.\n"
            "Output ONLY valid JSON. No markdown fences."
        )

    def _build_user_message(self, job_input: dict) -> str:
        return json.dumps(job_input, indent=2, default=str)

    def _output_schema(self) -> type[ScribeOutput]:
        return ScribeOutput


# ── Split approval gate ───────────────────────────────────────────────


def classify_patch_fields(patch: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check whether all CRM field patch keys are whitelisted.

    Returns (all_whitelisted, non_whitelisted_fields).
    """
    non_wl = [k for k in patch if k not in CRM_FIELD_WHITELIST]
    return (len(non_wl) == 0, non_wl)


def split_output(output: AgentOutput) -> tuple[dict, dict]:
    """Split scribe output into auto-approvable vs gated parts.

    Returns (auto_part, gated_part).
    - auto_part: summary, provenance_note, sql_checklist, three_whys,
                 s1_candidate — written immediately as CRM activity note
    - gated_part: next_steps, crm_fields_patch — sent to Review Queue
    """
    data = output.data

    auto_part = {
        "summary": data.get("summary", []),
        "sql_checklist": data.get("sql_checklist", {}),
        "three_whys": data.get("three_whys", {}),
        "s1_candidate": data.get("s1_candidate", False),
        "provenance_note": data.get("provenance_note", ""),
    }

    gated_part = {
        "next_steps": data.get("next_steps", []),
        "crm_fields_patch": data.get("crm_fields_patch", {}),
    }

    return auto_part, gated_part


# ── Pipeline ──────────────────────────────────────────────────────────


def process_crm_scribe_job(
    job_input: dict,
    *,
    crm_adapter: Any = None,
    policy_check: Any = None,
    create_job_fn: Any = None,
) -> AgentRunResult:
    """Full pipeline: run agent → split gate → auto-write note → queue gated.

    Parameters:
        job_input: The job's input_payload (transcript, meeting, account, contact)
        crm_adapter: CRMAdapter instance for write-back
        policy_check: callable(WriteBackAction) -> Verdict
        create_job_fn: callable(job_dict) -> Job for task creation
    """
    from app.adapters.interfaces.types import ActivityWrite
    from app.policy.guardrails import WriteBackAction
    from app.policy.guardrails import check as default_check

    agent = CRMScribeAgent()
    result = agent.run(job_input)

    if not result.success or result.output is None:
        return result

    # ── Stage-never-auto guardrail ──
    # Strip any stage-advancement fields the LLM might hallucinate
    _strip_stage_events(result.output)

    # ── Split gate ──
    auto_part, gated_part = split_output(result.output)
    check_fn = policy_check or default_check
    account_ref = job_input.get("account", {}).get("ref", "")

    # ── Auto-approve: write CRM activity note ──
    if crm_adapter and account_ref:
        note_body = _format_activity_note(auto_part)
        action = WriteBackAction(
            action_type="crm_note_log",
            account_ref=account_ref,
            is_customer_facing=False,
        )
        verdict = check_fn(action)
        if verdict.result.value == "ALLOW":

            activity = ActivityWrite(
                account_ref=account_ref,
                contact_ref=job_input.get("contact", {}).get("ref"),
                activity_type="note",
                subject="[BDR-OS] Call scribe",
                body=note_body,
            )
            _run_async(crm_adapter.log_activity(verdict, activity))

    # ── Gated: next_steps tasks + field patches → Review Queue ──
    patch = gated_part.get("crm_fields_patch", {})
    next_steps = gated_part.get("next_steps", [])
    all_whitelisted, non_wl_fields = classify_patch_fields(patch)

    # Determine policy for gated items
    needs_approval = False
    policy_flags: dict[str, Any] = {}

    if next_steps:
        needs_approval = True
        policy_flags["next_steps_queued"] = True

    if patch:
        if all_whitelisted:
            needs_approval = True
            policy_flags["field_patch_queued"] = True
        else:
            needs_approval = True
            policy_flags["field_patch_queued"] = True
            policy_flags["non_whitelisted_fields"] = non_wl_fields
            policy_flags["REQUIRE_APPROVAL"] = True

    if needs_approval:
        # Build a gated action for Review Queue
        gated_action = WriteBackAction(
            action_type="crm_scribe_gated",
            account_ref=account_ref,
            is_customer_facing=False,
        )
        check_fn(gated_action)

        # Store gated output for approval flow
        result.output.data["_gated"] = gated_part
        result.output.data["_policy_flags"] = policy_flags

        # Non-whitelisted fields always force REQUIRE_APPROVAL
        if not all_whitelisted:
            policy_flags["REQUIRE_APPROVAL"] = True

    return result


def _strip_stage_events(output: AgentOutput) -> None:
    """Remove any stage-advancement fields the LLM might produce.

    The scribe NEVER sets s1_reached, s2_reached, or ad_accepted.
    Only s1_candidate (a proposal flag) is allowed.
    """
    forbidden = {"s1_reached", "s2_reached", "ad_accepted", "ad_rejected"}
    data = output.data
    for key in forbidden:
        data.pop(key, None)

    # Also strip from crm_fields_patch
    patch = data.get("crm_fields_patch", {})
    for key in forbidden:
        patch.pop(key, None)

    # Strip from next_steps — remove any step that tries to emit stage events
    steps = data.get("next_steps", [])
    cleaned = []
    for step in steps:
        action_text = ""
        if isinstance(step, dict):
            action_text = step.get("action", "").lower()
        elif hasattr(step, "action"):
            action_text = step.action.lower()
        # Reject steps that try to set stage advancement
        if not any(f in action_text for f in ("set s1_reached", "set s2_reached", "set ad_accepted")):
            cleaned.append(step)
    data["next_steps"] = cleaned


def _format_activity_note(auto_part: dict) -> str:
    """Format the auto-approved part as a CRM activity note body."""
    lines = ["## Call Summary"]
    for bullet in auto_part.get("summary", []):
        lines.append(f"- {bullet}")

    lines.append("\n## Qualification (SQL Checklist)")
    checklist = auto_part.get("sql_checklist", {})
    if isinstance(checklist, dict):
        for k, v in checklist.items():
            mark = "✓" if v else "✗"
            lines.append(f"  {mark} {k}")

    lines.append("\n## Three Whys")
    whys = auto_part.get("three_whys", {})
    if isinstance(whys, dict):
        for k, v in whys.items():
            lines.append(f"- {k}: {v or '(not captured)'}")

    if auto_part.get("s1_candidate"):
        lines.append("\n⚑ s1_candidate: true (proposed for review)")

    if auto_part.get("provenance_note"):
        lines.append(f"\n## Provenance\n{auto_part['provenance_note']}")

    return "\n".join(lines)


def _run_async(coro: Any) -> Any:
    """Run a coroutine, handling both sync and async contexts."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        loop.create_task(coro)
        return None
    return asyncio.run(coro)
