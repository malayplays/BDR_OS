"""Reporting Agent — personal recap + manager update draft.

Spec: AGENTS.md §10
- Trigger: Friday 15:00; month-end
- Personal recap: auto-approved, archived to reports table + markdown export
- Manager update draft: approval REQUIRED (externally visible), Gmail draft write-back
- Month-end variant: goal pace vs. annual, rate deltas vs. benchmarks, cold-start exit
- Numbers come from FunnelState/Plan — the LLM narrates, it never computes
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentBase, AgentOutput, AgentRunResult

# ── Output schemas ────────────────────────────────────────────────────


class PersonalRecap(BaseModel):
    """Personal recap output — auto-approved."""

    summary: str
    plan_vs_actual_by_stage: dict[str, Any]
    rate_trends: list[dict[str, Any]]
    wins: list[str]
    at_risk_flags: list[str]
    next_week_plan_summary: str
    confidence: float
    needs_human_because: str | None = None


class ManagerUpdate(BaseModel):
    """Manager update draft for Kyle — approval REQUIRED."""

    subject: str
    body: str
    risk_section: str
    need_from_you: str
    confidence: float
    needs_human_because: str | None = None


# ── Trigger logic ─────────────────────────────────────────────────────


def is_friday_3pm(now: datetime) -> bool:
    """Friday 15:00 trigger (weekday 4 = Friday)."""
    return now.weekday() == 4 and now.hour == 15


def is_month_end(now: datetime, today: date | None = None) -> bool:
    """Month-end trigger: last business day of the month."""
    d = today or now.date()
    if d.month == 12:
        next_month_first = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_month_first = d.replace(month=d.month + 1, day=1)
    from datetime import timedelta

    last_day = next_month_first - timedelta(days=1)
    # Walk back from last day to last business day (Mon-Fri)
    while last_day.weekday() >= 5:
        last_day -= timedelta(days=1)
    return d == last_day


def should_trigger(now: datetime) -> dict[str, bool]:
    """Return which triggers fire at the given time."""
    return {
        "friday": is_friday_3pm(now),
        "month_end": is_month_end(now),
    }


# ── Number extraction / hallucination guard ───────────────────────────


def _normalize_number(val: str) -> str:
    """Normalize a numeric string: strip trailing zeros after decimal."""
    if "." in val:
        val = val.rstrip("0").rstrip(".")
    return val


def extract_numbers(text: str) -> set[str]:
    """Extract all numeric tokens from text (ints, floats, percentages stripped of %)."""
    raw = re.findall(r"-?\d+\.?\d*%?", text)
    cleaned: set[str] = set()
    for tok in raw:
        val = tok.rstrip("%")
        if val:
            cleaned.add(_normalize_number(val))
    return cleaned


def build_allowed_numbers(payload: dict) -> set[str]:
    """Recursively extract all numeric values from the input payload."""
    nums: set[str] = set()

    def _walk(obj: Any) -> None:
        if isinstance(obj, (int, float)):
            nums.add(_normalize_number(str(obj)))
            if isinstance(obj, float):
                if obj == int(obj):
                    nums.add(str(int(obj)))
            else:
                nums.add(f"{obj}.0")
                nums.add(_normalize_number(f"{obj}.0"))
        elif isinstance(obj, str):
            nums.update(extract_numbers(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                _walk(v)

    _walk(payload)
    return nums


def verify_numbers_not_hallucinated(output_text: str, input_payload: dict) -> tuple[bool, set[str]]:
    """Assert every numeric token in output exists in input payload.

    Returns (passed, hallucinated_numbers).
    """
    allowed = build_allowed_numbers(input_payload)
    output_nums = extract_numbers(output_text)
    hallucinated = output_nums - allowed
    return len(hallucinated) == 0, hallucinated


# ── Markdown rendering ────────────────────────────────────────────────


def render_recap_markdown(recap: PersonalRecap, trigger: str, period_label: str) -> str:
    """Render personal recap as archivable markdown."""
    lines = [
        f"# Personal Recap — {period_label}",
        f"_Trigger: {trigger}_\n",
        "## Summary",
        recap.summary,
        "",
        "## Plan vs Actual by Stage",
    ]
    for stage, data in recap.plan_vs_actual_by_stage.items():
        if isinstance(data, dict):
            plan_val = data.get("plan", "—")
            actual_val = data.get("actual", "—")
            lines.append(f"- **{stage}**: plan={plan_val}, actual={actual_val}")
        else:
            lines.append(f"- **{stage}**: {data}")

    lines.append("")
    lines.append("## Rate Trends")
    for rt in recap.rate_trends:
        metric = rt.get("metric", "?")
        current = rt.get("current", "?")
        confidence = rt.get("confidence", "?")
        lines.append(f"- {metric}: {current} (confidence: {confidence})")

    lines.append("")
    lines.append("## Wins")
    for w in recap.wins:
        lines.append(f"- {w}")

    lines.append("")
    lines.append("## At-Risk Flags")
    if recap.at_risk_flags:
        for f in recap.at_risk_flags:
            lines.append(f"- ⚠ {f}")
    else:
        lines.append("_None_")

    lines.append("")
    lines.append("## Next Week Plan")
    lines.append(recap.next_week_plan_summary)
    return "\n".join(lines)


def render_manager_markdown(update: ManagerUpdate) -> str:
    """Render manager update as markdown."""
    return (
        f"**Subject:** {update.subject}\n\n"
        f"{update.body}\n\n"
        f"**Risks:** {update.risk_section}\n\n"
        f"**Need from you:** {update.need_from_you}"
    )


# ── Agent ─────────────────────────────────────────────────────────────


class ReportingAgent(AgentBase):
    """Generates personal recap and manager update from funnel data."""

    agent_name = "reporting"

    def _system_prompt(self) -> str:
        return (
            "You are the Reporting Agent for a BDR automation system.\n\n"
            "You produce two outputs:\n"
            "1. **personal_recap**: points + earnings + persona mix, honest gaps, "
            "what's changing next week. Auto-approved, archived.\n"
            "2. **manager_update**: Short, outcome-first draft for Kyle. No activity-theater. "
            "Format: lead with held meetings vs target, booked pipeline, rate changes, "
            "risks (MUST include at_risk flags honestly), and one concrete 'need from you'.\n\n"
            "CRITICAL RULES:\n"
            "- You NARRATE numbers, you NEVER COMPUTE them. Every number in your output "
            "MUST exist verbatim in the input payload. If a number isn't in the input, "
            "you cannot use it. No rounding, no arithmetic, no derived values.\n"
            "- at_risk flags MUST appear in the manager update risk section. "
            "Suppressing risk flags is a bug.\n"
            "- The manager update must include a 'need_from_you' line — one concrete ask.\n"
            "- Month-end variant: also include goal pace vs. annual target, "
            "rate deltas vs. benchmarks, and cold-start exit progress.\n\n"
            "Output ONLY valid JSON matching the expected schema. No markdown, no explanation."
        )

    def _build_user_message(self, job_input: dict) -> str:
        return json.dumps(job_input, indent=2, default=str)

    def _output_schema(self) -> type[BaseModel]:
        return PersonalRecap  # default; manager_update uses ManagerUpdate

    def _build_recap_prompt(self, job_input: dict) -> str:
        return (
            f"{self._build_user_message(job_input)}\n\n"
            "Produce a personal_recap JSON with fields: summary, plan_vs_actual_by_stage, "
            "rate_trends (list of {{metric, current, confidence}}), wins (list of strings), "
            "at_risk_flags (list of strings), next_week_plan_summary, confidence, needs_human_because.\n"
            "Remember: every number in your output MUST exist in the input above."
        )

    def _build_manager_prompt(self, job_input: dict) -> str:
        return (
            f"{self._build_user_message(job_input)}\n\n"
            "Produce a manager_update JSON with fields: subject, body, risk_section, "
            "need_from_you, confidence, needs_human_because.\n"
            "Format: outcome-first, no activity-theater. Start with held vs target, booked pipeline.\n"
            "MANDATORY: at_risk flags from input MUST appear in risk_section — never suppress them.\n"
            "Include one concrete 'need_from_you' ask.\n"
            "Remember: every number in your output MUST exist in the input above."
        )

    def run_recap(self, job_input: dict) -> AgentRunResult:
        """Run personal recap generation."""
        system = self._assemble_system_prompt()
        user_message = self._build_recap_prompt(job_input)
        return self._run_with_schema(system, user_message, PersonalRecap)

    def run_manager_update(self, job_input: dict) -> AgentRunResult:
        """Run manager update generation."""
        system = self._assemble_system_prompt()
        user_message = self._build_manager_prompt(job_input)
        return self._run_with_schema(system, user_message, ManagerUpdate)

    def _run_with_schema(self, system: str, user_message: str, schema: type[BaseModel]) -> AgentRunResult:
        """Shared run logic with specified output schema."""
        import time

        from pydantic import ValidationError

        start = time.time()
        total_tokens_in = 0
        total_tokens_out = 0
        raw_output: str | None = None

        for attempt in range(2):
            try:
                raw, t_in, t_out = self._call_llm(system, user_message)
                total_tokens_in += t_in
                total_tokens_out += t_out
                raw_output = raw

                parsed = self._parse_output(raw, schema)
                output_data = parsed.model_dump()
                confidence = output_data.pop("confidence", 0.5)
                needs_human = output_data.pop("needs_human_because", None)

                agent_output = AgentOutput(
                    confidence=confidence,
                    needs_human_because=needs_human,
                    data=output_data,
                    raw_llm_output=raw,
                )
                duration_ms = int((time.time() - start) * 1000)
                return AgentRunResult(
                    output=agent_output,
                    success=True,
                    raw_output=raw,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    duration_ms=duration_ms,
                )
            except (json.JSONDecodeError, ValidationError) as e:
                if attempt == 0:
                    user_message = (
                        f"{user_message}\n\n"
                        f"[SYSTEM: Previous response was not valid JSON. Error: {e}. "
                        f"Respond with ONLY valid JSON.]"
                    )
                    continue
                duration_ms = int((time.time() - start) * 1000)
                return AgentRunResult(
                    output=None,
                    success=False,
                    error=f"Output validation failed after retry: {e}",
                    raw_output=raw_output,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                return AgentRunResult(
                    output=None,
                    success=False,
                    error=str(e),
                    raw_output=raw_output,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    duration_ms=duration_ms,
                )
        duration_ms = int((time.time() - start) * 1000)
        return AgentRunResult(
            output=None, success=False, error="Exhausted retries",
            raw_output=raw_output, tokens_in=total_tokens_in, tokens_out=total_tokens_out,
            duration_ms=duration_ms,
        )


# ── Pipeline ──────────────────────────────────────────────────────────


def process_reporting_job(
    job_input: dict,
    trigger: str,
    *,
    email_adapter: Any = None,
    create_report_fn: Any = None,
) -> dict[str, AgentRunResult]:
    """Full pipeline: recap (auto-approved + archived) + manager draft (gated).

    Parameters:
        job_input: payload with funnel_state, plan, rates, etc.
        trigger: "friday" or "month_end"
        email_adapter: EmailAdapter for Gmail draft write-back
        create_report_fn: callable(report_dict) to persist Report row

    Returns dict of {"recap": AgentRunResult, "manager_update": AgentRunResult}
    """
    agent = ReportingAgent()

    # 1. Personal recap — auto-approved, archived
    recap_result = agent.run_recap(job_input)
    if recap_result.success and recap_result.output and create_report_fn:
        recap_data = recap_result.output.data
        recap_obj = PersonalRecap(
            confidence=recap_result.output.confidence,
            needs_human_because=recap_result.output.needs_human_because,
            **recap_data,
        )
        period_label = job_input.get("period_label", "Weekly")
        md = render_recap_markdown(recap_obj, trigger, period_label)
        create_report_fn({
            "report_type": "personal_recap",
            "trigger": trigger,
            "goal_id": job_input.get("goal_id"),
            "payload": recap_data,
            "markdown": md,
            "approval_required": False,
            "approved": True,  # auto-approved
        })

    # 2. Manager update — approval REQUIRED, Gmail draft write-back
    manager_result = agent.run_manager_update(job_input)
    if manager_result.success and manager_result.output:
        update_data = manager_result.output.data
        update_obj = ManagerUpdate(
            confidence=manager_result.output.confidence,
            needs_human_because=manager_result.output.needs_human_because,
            **update_data,
        )
        write_back_ref = None
        if email_adapter:
            from app.adapters.interfaces.types import DraftEmail
            from app.models.enums import VerdictResult
            from app.schemas import Verdict

            draft = DraftEmail(
                to=["kyle@company.com"],
                subject=update_obj.subject,
                body=render_manager_markdown(update_obj),
            )
            verdict = Verdict(
                result=VerdictResult.REQUIRE_APPROVAL,
                reason="Manager update requires approval before sending.",
            )
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    write_back_ref = pool.submit(
                        asyncio.run, email_adapter.create_draft(verdict, draft)
                    ).result()
            else:
                write_back_ref = asyncio.run(email_adapter.create_draft(verdict, draft))

        if create_report_fn:
            create_report_fn({
                "report_type": "manager_update",
                "trigger": trigger,
                "goal_id": job_input.get("goal_id"),
                "payload": update_data,
                "markdown": render_manager_markdown(update_obj),
                "approval_required": True,
                "approved": False,  # sits in Review Queue
                "write_back_ref": write_back_ref,
            })

    return {"recap": recap_result, "manager_update": manager_result}
