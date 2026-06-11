"""Call Prep Agent — generates 5-min pre-call cards.

Spec: AGENTS.md §7
- Trigger: T−30min before any call block or booked meeting
- Inputs: meeting/call_block, brief, thread_history, transcript_refs, signal, funnel context
- Output: 5-min pre-call card ≤600 chars (phone screen)
- Approval: Auto (internal)
- Write-back: none (ephemeral, attached to Today screen job)
- Continuity rule: if thread has prior objection+response, last_interaction must instruct
  picking up that thread, not restarting pitch.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, field_validator

from app.agents.base import AgentBase, AgentRunResult


class Objection(BaseModel):
    objection: str
    response: str


class CallPrepCard(BaseModel):
    """Pre-call card output schema — ≤600 chars total when rendered."""

    who: str  # 2 lines max
    why_now: str  # 1 line
    last_interaction: str  # 1 line
    goal_of_call: str  # 1 line
    likely_objections: list[Objection]  # exactly 2
    the_one_thing_to_show: str
    confidence: float
    needs_human_because: str | None = None

    @field_validator("likely_objections")
    @classmethod
    def validate_objections_count(cls, v: list[Objection]) -> list[Objection]:
        if len(v) > 2:
            v = v[:2]
        return v

    def render(self) -> str:
        """Render card to display string for char-count budget check."""
        lines = [
            f"WHO: {self.who}",
            f"WHY NOW: {self.why_now}",
            f"LAST: {self.last_interaction}",
            f"GOAL: {self.goal_of_call}",
        ]
        for i, obj in enumerate(self.likely_objections, 1):
            lines.append(f"OBJ{i}: {obj.objection} → {obj.response}")
        lines.append(f"SHOW: {self.the_one_thing_to_show}")
        return "\n".join(lines)

    def char_count(self) -> int:
        return len(self.render())


class CallPrepAgent(AgentBase):
    """Generates 5-min pre-call cards for meetings and call blocks."""

    agent_name = "call_prep"

    def _system_prompt(self) -> str:
        return (
            "You are a Call Prep agent for a BDR automation system.\n\n"
            "Your job: given a meeting/call block context, produce a concise pre-call "
            "card that a BDR can glance at on their phone in 5 minutes before the call.\n\n"
            "CRITICAL CONSTRAINTS:\n"
            "- Total rendered card MUST be ≤600 characters. Be extremely concise.\n"
            "- who: 2 lines max (name, title, company context)\n"
            "- why_now: 1 sentence\n"
            "- last_interaction: 1 sentence summarizing last touch. If no prior "
            "contact, write '(no prior contact)'\n"
            "- goal_of_call: 1 sentence\n"
            "- likely_objections: exactly 2, each with a short response\n"
            "- the_one_thing_to_show: 1 sentence\n\n"
            "CONTINUITY RULE (critical): If the thread_history contains a prior "
            "objection and your response/reframe, last_interaction MUST instruct "
            "the BDR to pick up that thread and continue from where you left off. "
            "Never instruct restarting the pitch.\n\n"
            "GRACEFUL DEGRADATION: If any input is missing (no brief, no thread, "
            "no transcript), still produce a valid card. Mark gaps with "
            "'(no prior contact)' or similar short notes.\n\n"
            "Output ONLY valid JSON matching the CallPrepCard schema. "
            "No markdown, no explanation."
        )

    def _build_user_message(self, job_input: dict) -> str:
        return json.dumps(job_input, indent=2, default=str)

    def _output_schema(self) -> type[CallPrepCard]:
        return CallPrepCard

    def run(self, job_input: dict) -> AgentRunResult:
        """Run with ≤600-char enforcement: regenerate once on overflow."""
        result = super().run(job_input)

        if not result.success or result.output is None:
            return result

        card = CallPrepCard.model_validate(
            {
                "confidence": result.output.confidence,
                "needs_human_because": result.output.needs_human_because,
                **result.output.data,
            }
        )
        if card.char_count() > 600:
            tighter_input = {
                **job_input,
                "_retry_hint": (
                    "PREVIOUS OUTPUT EXCEEDED 600 CHARACTERS when rendered. "
                    "Be drastically more concise. Max 600 chars total rendered."
                ),
            }
            result = super().run(tighter_input)
            if not result.success or result.output is None:
                return result
            card = CallPrepCard.model_validate(
                {
                    "confidence": result.output.confidence,
                    "needs_human_because": result.output.needs_human_because,
                    **result.output.data,
                }
            )
            if card.char_count() > 600:
                result.success = False
                cc = card.char_count()
                result.error = f"Card exceeds 600 chars ({cc} chars) after regeneration"
                return result

        return result


def should_trigger_call_prep(meeting_start: datetime, now: datetime) -> bool:
    """Return True if now is within the T-30min trigger window."""
    trigger_time = meeting_start - timedelta(minutes=30)
    return now >= trigger_time


def build_call_prep_job(
    meeting: dict,
    *,
    brief: dict | None = None,
    thread_history: list[dict] | None = None,
    transcript_refs: list[dict] | None = None,
    signal: dict | None = None,
    funnel_context: dict | None = None,
    meeting_start: datetime | None = None,
) -> dict:
    """Build the call_prep job payload, attached to the Today-screen entry.

    Returns a job dict that is ephemeral (auto-approved, no write-back).
    """
    input_payload: dict[str, Any] = {"meeting": meeting}
    if brief is not None:
        input_payload["brief"] = brief
    if thread_history is not None:
        input_payload["thread_history"] = thread_history
    if transcript_refs is not None:
        input_payload["transcript_refs"] = transcript_refs
    if signal is not None:
        input_payload["signal"] = signal
    if funnel_context is not None:
        input_payload["funnel_context"] = funnel_context

    return {
        "job_type": "call_prep",
        "funnel_stage": "create",
        "agent": "call_prep",
        "account_ref": meeting.get("account_ref"),
        "contact_ref": meeting.get("contact_ref"),
        "trigger": {
            "type": "timer",
            "rule": "T-30min before meeting",
            "meeting_ref": meeting.get("ref"),
            "meeting_start": meeting_start.isoformat() if meeting_start else None,
        },
        "input_payload": input_payload,
        "approval_gate": "auto",
        "ephemeral": True,
        "today_entry_ref": meeting.get("ref"),
    }


def process_call_prep_job(job_input: dict) -> AgentRunResult:
    """Run the call prep agent — no write-back, auto-approved, ephemeral."""
    agent = CallPrepAgent()
    return agent.run(job_input)
