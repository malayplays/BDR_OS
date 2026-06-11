"""Book Response Agent — Speed-to-Book (triage's chained twin).

Spec: AGENTS.md S4
- Trigger: positive_reply event (chained from inbox_triage)
- Output: BookDraft{reply_in_thread <=60 words, acknowledges their words,
          2 concrete times + link, in SAME thread}
- Approval: REQUIRED (customer-facing), top of Review Queue with 4h SLA countdown
- Write-back: create_draft in-thread; on booking -> show-rate machine
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentBase, AgentRunResult

logger = logging.getLogger(__name__)


# ── Pydantic schemas ──────────────────────────────────────────────────

class BookDraft(BaseModel):
    """Speed-to-book draft reply."""
    reply_body: str  # <=60 words, in-thread
    slot_1: str  # e.g. "Thu Jun 12 2:00 PM"
    slot_2: str  # e.g. "Fri Jun 13 10:30 AM"
    booking_link: str
    acknowledges_their_words: bool
    confidence: float
    needs_human_because: str | None = None

    def word_count(self) -> int:
        return len(self.reply_body.split())


class BookResponseAgent(AgentBase):
    """Generates speed-to-book draft replies for positive inbound."""

    agent_name = "book_response"

    def _system_prompt(self) -> str:
        return (
            "You are a Speed-to-Book agent for a BDR automation system.\n\n"
            "Your job: draft a reply to a positive inbound message that books a meeting.\n\n"
            "CRITICAL CONSTRAINTS:\n"
            "- Reply <=60 words\n"
            "- MUST acknowledge what they said (reference their words)\n"
            "- Offer exactly 2 concrete time slots (prefer <=4 days out)\n"
            "- Include exactly 1 booking link\n"
            "- Reply goes in the SAME thread\n"
            "- Pull toward sooner slots when possible ('sooner usually beats calendar-tetris')\n"
            "- Give agency ('if either works', 'grabbing one')\n"
            "- If they asked for info first: one-line value tease + pivot to meeting\n\n"
            "Output ONLY valid JSON matching the BookDraft schema."
        )

    def _build_user_message(self, job_input: dict) -> str:
        return json.dumps(job_input, indent=2, default=str)

    def _output_schema(self) -> type[BookDraft]:
        return BookDraft


# ── Slot selection ─────────────────────────────────────────────────────

def select_slots(
    available_slots: list[dict],
    *,
    prefer_max_days_out: int = 4,
    count: int = 2,
) -> list[dict]:
    """Select best slots: prefer <=4 days out, sorted by days_out ascending.

    Returns up to `count` slots.
    """
    if not available_slots:
        return []

    preferred = [s for s in available_slots if s.get("days_out", 999) <= prefer_max_days_out]
    if len(preferred) >= count:
        preferred.sort(key=lambda s: s.get("days_out", 999))
        return preferred[:count]

    # Not enough preferred slots — include farther ones
    all_sorted = sorted(available_slots, key=lambda s: s.get("days_out", 999))
    return all_sorted[:count]


def format_slot(slot: dict) -> str:
    """Format a slot dict into human-readable string."""
    start = slot.get("start", "")
    if isinstance(start, str):
        try:
            dt = datetime.fromisoformat(start)
            return dt.strftime("%a %b %d %-I:%M %p")
        except ValueError:
            return start
    if isinstance(start, datetime):
        return start.strftime("%a %b %d %-I:%M %p")
    return str(start)


# ── Draft builder (deterministic / mock mode) ──────────────────────────

def build_book_draft_deterministic(
    *,
    their_words: str,
    slots: list[dict],
    booking_link: str = "https://cal.example.com/malay/15min",
    prefer_max_days_out: int = 4,
) -> dict:
    """Build a BookDraft without LLM — deterministic for testing."""
    selected = select_slots(slots, prefer_max_days_out=prefer_max_days_out)

    slot_1_str = format_slot(selected[0]) if len(selected) > 0 else "TBD"
    slot_2_str = format_slot(selected[1]) if len(selected) > 1 else "TBD"

    # Acknowledge + offer slots
    # Check if they asked for info
    lower = their_words.lower()
    info_ask = any(kw in lower for kw in ["more info", "send info", "send me info"])

    if info_ask:
        reply = (
            f"Easiest way to see it is 15 min where I show it on a ticket like yours "
            f"-- {slot_1_str} or {slot_2_str}? Grabbing one: {booking_link}"
        )
    else:
        reply = (
            f"Great -- I have {slot_1_str} or {slot_2_str} (15 min) "
            f"if either works; sooner usually beats calendar-tetris. "
            f"Grabbing one: {booking_link}"
        )

    return {
        "reply_body": reply,
        "slot_1": slot_1_str,
        "slot_2": slot_2_str,
        "booking_link": booking_link,
        "acknowledges_their_words": True,
        "confidence": 0.9,
        "needs_human_because": None,
    }


# ── Full pipeline ──────────────────────────────────────────────────────

def process_book_response_job(
    job_input: dict,
    *,
    calendar_adapter: Any = None,
    use_llm: bool = False,
) -> AgentRunResult:
    """Full pipeline: select slots -> build draft -> return for approval.

    The draft is ALWAYS approval-required (customer-facing).
    """
    message = job_input.get("message", {})
    their_words = message.get("body", "")

    # Get slots from input or calendar adapter
    slots = job_input.get("slots", [])

    if not use_llm:
        draft_data = build_book_draft_deterministic(
            their_words=their_words,
            slots=slots,
        )
        from app.agents.base import AgentOutput

        output = AgentOutput(
            confidence=draft_data.get("confidence", 0.9),
            needs_human_because=draft_data.get("needs_human_because"),
            data=draft_data,
        )
        return AgentRunResult(output=output, success=True)

    # LLM path
    agent = BookResponseAgent()
    return agent.run(job_input)
