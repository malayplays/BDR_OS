"""AgentBase — shared foundation for all BDR-OS agents.

Every agent implements `run(job) -> AgentOutput` with:
- Prompt assembly (system + voice_profile + value_props + job input)
- Anthropic API client (model from agents.yaml, env key)
- JSON-schema-validated output parsing with one retry on parse failure
- confidence + needs_human_because on every output
- Token/cost logging per run
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

AGENTS_DIR = Path(__file__).resolve().parent
AGENTS_YAML = AGENTS_DIR / "agents.yaml"
VOICE_PROFILE_PATH = AGENTS_DIR / "voice_profile.md"
VALUE_PROPS_PATH = AGENTS_DIR / "value_props.yaml"


class AgentOutput(BaseModel):
    """Base output envelope for all agents."""

    confidence: float
    needs_human_because: str | None = None
    data: dict[str, Any]
    raw_llm_output: str | None = None


class AgentRunResult(BaseModel):
    """Full result including metadata from a run."""

    output: AgentOutput | None = None
    success: bool = True
    error: str | None = None
    raw_output: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


def _load_agents_config() -> dict:
    if AGENTS_YAML.exists():
        with open(AGENTS_YAML) as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_model_config(agent_name: str) -> dict:
    cfg = _load_agents_config()
    defaults = cfg.get("defaults", {})
    agent_cfg = cfg.get("agents", {}).get(agent_name, {})
    return {**defaults, **agent_cfg}


def _load_voice_profile() -> str:
    if VOICE_PROFILE_PATH.exists():
        return VOICE_PROFILE_PATH.read_text()
    return ""


def _load_value_props() -> str:
    if VALUE_PROPS_PATH.exists():
        return VALUE_PROPS_PATH.read_text()
    return ""


def _estimate_cost(tokens_in: int, tokens_out: int, model: str) -> float:
    """Rough cost estimate based on Anthropic pricing."""
    # Claude 3.5 Sonnet pricing: $3/M input, $15/M output
    if "sonnet" in model:
        return (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000
    # Claude 3.5 Haiku: $0.25/M input, $1.25/M output
    if "haiku" in model:
        return (tokens_in * 0.25 + tokens_out * 1.25) / 1_000_000
    # Default to sonnet pricing
    return (tokens_in * 3.0 + tokens_out * 15.0) / 1_000_000


class AgentBase(ABC):
    """Abstract base class for all BDR-OS agents."""

    agent_name: str = "base"

    def __init__(self) -> None:
        self._config = _get_model_config(self.agent_name)
        self._voice_profile = _load_voice_profile()
        self._value_props = _load_value_props()

    @abstractmethod
    def _system_prompt(self) -> str:
        """Return the system prompt specific to this agent."""
        ...

    @abstractmethod
    def _build_user_message(self, job_input: dict) -> str:
        """Build the user message from job input payload."""
        ...

    @abstractmethod
    def _output_schema(self) -> type[T]:
        """Return the Pydantic model class for validating LLM output."""
        ...

    def _assemble_system_prompt(self) -> str:
        """Assemble full system prompt: agent-specific + voice + value_props."""
        parts = [self._system_prompt()]
        if self._voice_profile:
            parts.append(f"\n\n## Voice Profile\n{self._voice_profile}")
        if self._value_props:
            parts.append(f"\n\n## Value Propositions\n{self._value_props}")
        return "\n".join(parts)

    def _parse_output(self, raw: str, schema: type[T]) -> T:
        """Parse JSON from LLM output and validate against schema."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        data = json.loads(text)
        return schema.model_validate(data)

    def _call_llm(self, system: str, user_message: str) -> tuple[str, int, int]:
        """Call Anthropic API. Returns (response_text, tokens_in, tokens_out)."""
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        model = self._config.get("model", "claude-sonnet-4-20250514")
        max_tokens = self._config.get("max_tokens", 2048)
        temperature = self._config.get("temperature", 0.3)

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        return text, tokens_in, tokens_out

    def run(self, job_input: dict) -> AgentRunResult:
        """Execute the agent: prompt → LLM → parse → validate (with 1 retry)."""
        start = time.time()
        system = self._assemble_system_prompt()
        user_message = self._build_user_message(job_input)
        schema = self._output_schema()

        total_tokens_in = 0
        total_tokens_out = 0
        raw_output: str | None = None

        for attempt in range(2):  # max 1 retry
            try:
                raw, t_in, t_out = self._call_llm(system, user_message)
                total_tokens_in += t_in
                total_tokens_out += t_out
                raw_output = raw

                parsed = self._parse_output(raw, schema)
                output_data = parsed.model_dump()

                # Extract confidence and needs_human_because from parsed output
                confidence = output_data.pop("confidence", 0.5)
                needs_human = output_data.pop("needs_human_because", None)

                agent_output = AgentOutput(
                    confidence=confidence,
                    needs_human_because=needs_human,
                    data=output_data,
                    raw_llm_output=raw,
                )

                duration_ms = int((time.time() - start) * 1000)
                model = self._config.get("model", "claude-sonnet-4-20250514")
                cost = _estimate_cost(total_tokens_in, total_tokens_out, model)

                logger.info(
                    "Agent %s completed: confidence=%.2f, tokens_in=%d, tokens_out=%d, cost=$%.4f, duration=%dms",
                    self.agent_name,
                    confidence,
                    total_tokens_in,
                    total_tokens_out,
                    cost,
                    duration_ms,
                )

                return AgentRunResult(
                    output=agent_output,
                    success=True,
                    raw_output=raw,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    cost_usd=cost,
                    duration_ms=duration_ms,
                )

            except (json.JSONDecodeError, ValidationError) as e:
                if attempt == 0:
                    logger.warning(
                        "Agent %s parse failure (attempt %d), retrying: %s",
                        self.agent_name,
                        attempt + 1,
                        str(e),
                    )
                    # Retry with a hint to fix JSON
                    user_message = (
                        f"{user_message}\n\n"
                        f"[SYSTEM: Your previous response was not valid JSON. "
                        f"Error: {e}. Please respond with ONLY valid JSON matching the schema.]"
                    )
                    continue
                else:
                    # Hard fail after retry
                    duration_ms = int((time.time() - start) * 1000)
                    model = self._config.get("model", "claude-sonnet-4-20250514")
                    cost = _estimate_cost(total_tokens_in, total_tokens_out, model)
                    logger.error(
                        "Agent %s hard fail after retry: %s", self.agent_name, str(e)
                    )
                    return AgentRunResult(
                        output=None,
                        success=False,
                        error=f"Output validation failed after retry: {e}",
                        raw_output=raw_output,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        cost_usd=cost,
                        duration_ms=duration_ms,
                    )

            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                model = self._config.get("model", "claude-sonnet-4-20250514")
                cost = _estimate_cost(total_tokens_in, total_tokens_out, model)
                logger.error("Agent %s unexpected error: %s", self.agent_name, str(e))
                return AgentRunResult(
                    output=None,
                    success=False,
                    error=str(e),
                    raw_output=raw_output,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    cost_usd=cost,
                    duration_ms=duration_ms,
                )

        # Should not reach here, but safety net
        duration_ms = int((time.time() - start) * 1000)
        return AgentRunResult(
            output=None,
            success=False,
            error="Exhausted retries",
            raw_output=raw_output,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            duration_ms=duration_ms,
        )
