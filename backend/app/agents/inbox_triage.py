"""Inbox Triage Agent — classifies inbound replies and chains next jobs.

Spec: AGENTS.md S3
- Trigger: Every inbound via watch_replies (continuous, 60s default)
- Output: Triage{classification, urgency, extracted, next_job}
- Approval: Auto (classification only); chained jobs carry own gates
- Write-back: EventLog (reply_received / positive_reply); CRM activity;
  unsubscribe -> suppression list immediately (compliance fast-path)
- SLA: positive reply -> book_response job within minutes, due_at = +4h
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.agents.base import AgentBase

logger = logging.getLogger(__name__)

# ── Classification labels ──────────────────────────────────────────────

CLASSIFICATIONS = frozenset({
    "positive",
    "objection",
    "question",
    "referral",
    "ooo",
    "unsubscribe",
    "bounce",
    "spam",
})

URGENCY_LEVELS = frozenset({"now", "today", "this_week"})

# ── Deterministic keyword classifier (mock / fallback) ─────────────────

_POSITIVE_PATTERNS = [
    r"\byes\b",
    r"\bsure\b",
    r"\blet'?s\s+(set\s+up|connect|chat|talk|do\s+it|go\s+ahead|schedule)\b",
    r"\bhappy\s+to\s+(chat|connect|talk|meet)\b",
    r"\bopen\s+to\b",
    r"\bsounds?\s+(good|great|interesting|relevant|timely)\b",
    r"\binterested\b",
    r"\bset\s+up\s+time\b",
    r"\bsend\s+me\s+(some\s+)?times?\b",
    r"\bwhat\s+does\s+.*\blook\b",
    r"\bshow\s+me\b",
    r"\bquick\s+demo\b",
    r"\b15\s*min",
    r"\bsend\s+(me\s+)?(more\s+)?info\b",
    r"\bcan\s+you\s+send\s+(me\s+)?(some\s+)?(more\s+)?info\b",
    r"\bmore\s+info\b",
]

_UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove\s+me\b",
    r"\btake\s+me\s+off\b",
    r"\bstop\s+(emailing|contacting|messaging)\b",
    r"\bopt\s*out\b",
    r"\bdo\s+not\s+contact\b",
    r"\bno\s+more\s+(emails?|messages?)\b",
    r"\boff\s+your\s+list\b",
]

_OOO_PATTERNS = [
    r"\bout\s+of\s+(the\s+)?office\b",
    r"\booo\b",
    r"\b(away|on\s+leave|on\s+vacation|out\s+of\s+town)\b.*\b(until|returning|back)\b",
    r"\bautomatic\s+reply\b",
    r"\bI'?ll\s+be\s+(away|out)\b",
    r"\bcurrently\s+(away|out\s+of)\b",
    r"\blimited\s+access\s+to\s+email\b",
]

_BOUNCE_PATTERNS = [
    r"\bundeliverable\b",
    r"\bbounce\b",
    r"\bcould\s+not\s+be\s+(found|delivered)\b",
    r"\baddress\s+(rejected|not\s+found)\b",
    r"\bmailer-?daemon\b",
    r"\bno\s+such\s+user\b",
    r"\bmailbox\s+not\s+found\b",
]

_SPAM_PATTERNS = [
    r"\b(viagra|lottery|winner|prize|claim\s+your)\b",
    r"\b(nigerian|prince)\b",
]

_REFERRAL_PATTERNS = [
    r"\btalk\s+to\b.*\binstead\b",
    r"\breach\s+out\s+to\b",
    r"\bforward(ed)?\s+(this\s+)?to\b",
    r"\bloop(ed|ing)?\s+in\b",
    r"\bcc'?d\b",
    r"\byou\s+should\s+(contact|speak|talk)\s+(to|with)\b",
    r"\bbetter\s+person\s+(to\s+talk|for\s+this)\b",
]

_OBJECTION_PATTERNS = [
    r"\bnot\s+interested\b",
    r"\bno\s+thanks?\b",
    r"\bbudget\s+(is\s+)?locked\b",
    r"\bnot\s+a\s+priority\b",
    r"\balready\s+(use|have|using)\b",
    r"\btoo\s+small\b",
    r"\bhappy\s+with\s+(our|current)\b",
    r"\bdidn'?t\s+work\b",
    r"\bnot\s+(right\s+)?now\b",
    r"\breach\s+out\s+again\s+in\b",
    r"\btried\s+something\s+similar\b",
    r"\breview\s+when\s+I\s+have\s+time\b",
]

_QUESTION_PATTERNS = [
    r"\bhow\s+(does|do|much|long|is)\b",
    r"\bwhat\s+(is|are|does)\b",
    r"\bcan\s+(it|you|this)\b.*\?",
    r"\bdo\s+you\s+(support|integrate|offer|have)\b",
    r"\btell\s+me\s+(about|more)\b",
]

_OOO_DATE_PATTERNS = [
    r"(?:until|returning|return\s+(?:on|by)?|back\s+(?:on|by)?)\s+(\w+\s+\d{1,2}(?:,?\s+\d{4})?)",
    r"(?:until|returning|return\s+(?:on|by)?|back\s+(?:on|by)?)\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
    r"(?:until|returning|return\s+(?:on|by)?|back\s+(?:on|by)?)\s+(\d{4}-\d{2}-\d{2})",
]

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}


def _extract_ooo_return_date(text: str) -> str | None:
    """Try to extract a return date from OOO text."""
    for pattern in _OOO_DATE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1).strip().rstrip(".")
            # Try ISO format
            try:
                return datetime.fromisoformat(raw).strftime("%Y-%m-%d")
            except ValueError:
                pass
            # Try "Month Day" or "Month Day, Year"
            parts = re.split(r"[\s,]+", raw)
            if len(parts) >= 2:
                month_str = parts[0].lower()
                if month_str in MONTH_MAP:
                    month = MONTH_MAP[month_str]
                    try:
                        day = int(parts[1])
                    except ValueError:
                        continue
                    year = 2026
                    if len(parts) >= 3:
                        try:
                            year = int(parts[2])
                        except ValueError:
                            pass
                    try:
                        return datetime(year, month, day).strftime("%Y-%m-%d")
                    except ValueError:
                        pass
    return None


def _match_any(text: str, patterns: list[str]) -> bool:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


def classify_deterministic(body: str, subject: str = "") -> dict:
    """Rule-based classifier — 30/30 on fixtures, used for mock/deterministic mode."""
    text = f"{subject} {body}".strip()
    lower = text.lower()

    # Order: unsubscribe > bounce > spam > ooo > objection > referral > positive > question
    # Objection before positive so "not interested" beats the substring "interested".
    if _match_any(lower, _UNSUBSCRIBE_PATTERNS):
        return {
            "classification": "unsubscribe",
            "urgency": "now",
            "extracted": {},
            "next_job": None,
        }

    if _match_any(lower, _BOUNCE_PATTERNS):
        return {
            "classification": "bounce",
            "urgency": "today",
            "extracted": {},
            "next_job": None,
        }

    if _match_any(lower, _SPAM_PATTERNS):
        return {
            "classification": "spam",
            "urgency": "this_week",
            "extracted": {},
            "next_job": None,
        }

    if _match_any(lower, _OOO_PATTERNS):
        return_date = _extract_ooo_return_date(text)
        return {
            "classification": "ooo",
            "urgency": "this_week",
            "extracted": {"ooo_return_date": return_date},
            "next_job": "reschedule_touch",
        }

    if _match_any(lower, _OBJECTION_PATTERNS):
        return {
            "classification": "objection",
            "urgency": "today",
            "extracted": {},
            "next_job": "handle_objection",
        }

    if _match_any(lower, _REFERRAL_PATTERNS):
        return {
            "classification": "referral",
            "urgency": "today",
            "extracted": {},
            "next_job": "research_brief",
        }

    if _match_any(lower, _POSITIVE_PATTERNS):
        return {
            "classification": "positive",
            "urgency": "now",
            "extracted": {},
            "next_job": "book_response",
        }

    if _match_any(lower, _QUESTION_PATTERNS):
        return {
            "classification": "question",
            "urgency": "today",
            "extracted": {},
            "next_job": "answer_question",
        }

    # Default: objection (conservative)
    return {
        "classification": "objection",
        "urgency": "this_week",
        "extracted": {},
        "next_job": "handle_objection",
    }


# ── Pydantic schemas ──────────────────────────────────────────────────

class Triage(BaseModel):
    """Inbox triage output schema."""
    classification: str  # positive|objection|question|referral|ooo|unsubscribe|bounce|spam
    urgency: str  # now|today|this_week
    extracted: dict  # objection_type?, referred_to?, ooo_return_date?
    next_job: str | None  # job type to chain
    confidence: float
    needs_human_because: str | None = None


class InboxTriageAgent(AgentBase):
    """Classifies inbound messages and determines next action."""

    agent_name = "inbox_triage"

    def _system_prompt(self) -> str:
        return (
            "You are an Inbox Triage agent for a BDR automation system.\n\n"
            "Your job: classify an inbound email reply and determine next action.\n\n"
            "CLASSIFICATIONS:\n"
            "- positive: interested, wants to meet, asks for info (booking opportunity!)\n"
            "  CRITICAL: 'send more info' = positive (booking opportunity), NOT literature-send\n"
            "- objection: explicit pushback, not interested, timing/budget issues\n"
            "- question: asks about product/capabilities without clear interest signal\n"
            "- referral: redirects to another person\n"
            "- ooo: out-of-office / away auto-reply\n"
            "- unsubscribe: wants off the list / do not contact\n"
            "- bounce: undeliverable / address not found\n"
            "- spam: irrelevant / junk\n\n"
            "URGENCY: now (positive/unsubscribe), today (most replies), this_week (low priority)\n\n"
            "CHAINING TABLE:\n"
            "- positive -> book_response (SLA: +4h)\n"
            "- objection -> handle_objection\n"
            "- question -> answer_question\n"
            "- referral -> research_brief\n"
            "- ooo -> reschedule_touch (extract return date!)\n"
            "- unsubscribe -> null (suppression handled in pipeline)\n"
            "- bounce -> null\n"
            "- spam -> null\n\n"
            "EXTRACTED fields: ooo_return_date (YYYY-MM-DD if detectable), "
            "objection_type, referred_to\n\n"
            "Output ONLY valid JSON matching the Triage schema."
        )

    def _build_user_message(self, job_input: dict) -> str:
        return json.dumps(job_input, indent=2, default=str)

    def _output_schema(self) -> type[Triage]:
        return Triage

    def classify(
        self, message_body: str, subject: str = "", *, use_llm: bool = False, job_input: dict | None = None,
    ) -> dict:
        """Classify a message. use_llm=False for deterministic mode."""
        if not use_llm:
            return classify_deterministic(message_body, subject)

        # LLM path
        inp = job_input or {"message": {"body": message_body, "subject": subject}}
        result = self.run(inp)
        if result.success and result.output:
            return result.output.data
        # Fallback to deterministic on LLM failure
        return classify_deterministic(message_body, subject)


# ── Chaining / next_job helpers ────────────────────────────────────────

CLASSIFICATION_CHAIN_MAP: dict[str, str | None] = {
    "positive": "book_response",
    "objection": "handle_objection",
    "question": "answer_question",
    "referral": "research_brief",
    "ooo": "reschedule_touch",
    "unsubscribe": None,
    "bounce": None,
    "spam": None,
}


def build_chained_job(
    triage: dict,
    *,
    message: dict,
    contact_ref: str | None = None,
    account_ref: str | None = None,
    thread_ref: str | None = None,
    positive_reply_at: datetime | None = None,
) -> dict | None:
    """Build the next job payload from triage result."""
    classification = triage.get("classification", "")
    next_job = triage.get("next_job") or CLASSIFICATION_CHAIN_MAP.get(classification)
    if not next_job:
        return None

    job: dict[str, Any] = {
        "job_type": next_job,
        "funnel_stage": "convert",
        "agent": next_job,
        "account_ref": account_ref,
        "contact_ref": contact_ref,
        "trigger": {
            "type": "inbox_triage",
            "classification": classification,
            "message_id": message.get("id"),
        },
        "input_payload": {
            "triage": triage,
            "message": message,
            "thread_ref": thread_ref,
        },
    }

    if classification == "positive":
        # SLA: due_at = positive_reply + 4h
        reply_at = positive_reply_at or datetime.utcnow()
        job["due_at"] = (reply_at + timedelta(hours=4)).isoformat()
        job["priority_score"] = 100.0  # top of queue
        job["input_payload"]["sla_deadline"] = job["due_at"]

    if classification == "ooo":
        return_date = triage.get("extracted", {}).get("ooo_return_date")
        job["input_payload"]["ooo_return_date"] = return_date
        if return_date:
            # Schedule for return date + 1 day
            try:
                rd = datetime.strptime(return_date, "%Y-%m-%d")
                job["due_at"] = (rd + timedelta(days=1)).isoformat()
            except ValueError:
                pass

    return job


# ── Suppression list (compliance fast-path) ────────────────────────────

_suppression_list: set[str] = set()


def get_suppression_list() -> set[str]:
    return _suppression_list


def add_to_suppression_list(contact_ref: str) -> None:
    _suppression_list.add(contact_ref)


def is_suppressed(contact_ref: str) -> bool:
    return contact_ref in _suppression_list


def clear_suppression_list() -> None:
    """For testing only."""
    _suppression_list.clear()


def handle_unsubscribe(
    contact_ref: str,
    *,
    db_session: Any = None,
    skip_jobs_fn: Any = None,
) -> dict:
    """Compliance fast-path: suppress contact, kill all pending jobs/sequences.

    No approval needed — suppressive direction only (policy.yaml exception).
    """
    add_to_suppression_list(contact_ref)

    skipped_jobs: list[str] = []
    if skip_jobs_fn:
        skipped_jobs = skip_jobs_fn(contact_ref)

    return {
        "suppressed": True,
        "contact_ref": contact_ref,
        "skipped_jobs": skipped_jobs,
    }


# ── OOO pause (not kill) ──────────────────────────────────────────────

def handle_ooo(
    contact_ref: str,
    return_date: str | None,
    *,
    pause_sequence_fn: Any = None,
) -> dict:
    """OOO: pause sequence (not kill), schedule reschedule_touch at return+1d."""
    paused = False
    if pause_sequence_fn:
        paused = pause_sequence_fn(contact_ref, return_date)

    return {
        "paused": paused,
        "contact_ref": contact_ref,
        "return_date": return_date,
    }


# ── Poll pipeline ──────────────────────────────────────────────────────

async def poll_and_triage(
    email_adapter: Any,
    since: datetime,
    *,
    use_llm: bool = False,
    create_job_fn: Any = None,
    create_event_fn: Any = None,
    db_session: Any = None,
    skip_jobs_fn: Any = None,
    pause_sequence_fn: Any = None,
) -> list[dict]:
    """One poll cycle: fetch replies, classify, chain next jobs, write events.

    Returns list of triage results with chained job info.
    """
    messages = await email_adapter.watch_replies(since)
    agent = InboxTriageAgent()
    results: list[dict] = []

    for msg in messages:
        msg_dict = msg.model_dump(mode="json") if hasattr(msg, "model_dump") else msg

        triage = agent.classify(
            msg_dict.get("body", ""),
            msg_dict.get("subject", ""),
            use_llm=use_llm,
        )

        classification = triage["classification"]
        contact_ref = msg_dict.get("sender")
        account_ref = msg_dict.get("account_ref")
        thread_ref = msg_dict.get("thread_ref")
        received_at_str = msg_dict.get("received_at")
        received_at = (
            datetime.fromisoformat(received_at_str)
            if received_at_str
            else datetime.utcnow()
        )

        # EventLog: reply_received always
        if create_event_fn:
            create_event_fn({
                "event_type": "reply_received",
                "account_ref": account_ref or "",
                "contact_ref": contact_ref,
                "occurred_at": received_at.isoformat(),
                "source": "email",
                "payload": {"classification": classification, "message_id": msg_dict.get("id")},
            })

        # EventLog: positive_reply on positive classification
        if classification == "positive" and create_event_fn:
            create_event_fn({
                "event_type": "positive_reply",
                "account_ref": account_ref or "",
                "contact_ref": contact_ref,
                "occurred_at": received_at.isoformat(),
                "source": "email",
                "payload": {"message_id": msg_dict.get("id")},
            })

        # Compliance fast-path: unsubscribe
        if classification == "unsubscribe":
            handle_unsubscribe(
                contact_ref or "",
                db_session=db_session,
                skip_jobs_fn=skip_jobs_fn,
            )
            if create_event_fn:
                create_event_fn({
                    "event_type": "unsubscribe",
                    "account_ref": account_ref or "",
                    "contact_ref": contact_ref,
                    "occurred_at": received_at.isoformat(),
                    "source": "email",
                    "payload": {"message_id": msg_dict.get("id")},
                })

        # OOO pause
        if classification == "ooo":
            return_date = triage.get("extracted", {}).get("ooo_return_date")
            handle_ooo(
                contact_ref or "",
                return_date,
                pause_sequence_fn=pause_sequence_fn,
            )

        # Chain next job
        chained_job = build_chained_job(
            triage,
            message=msg_dict,
            contact_ref=contact_ref,
            account_ref=account_ref,
            thread_ref=thread_ref,
            positive_reply_at=received_at,
        )

        if chained_job and create_job_fn:
            # Factory-level suppression check
            job_contact = chained_job.get("contact_ref", "")
            if not is_suppressed(job_contact):
                create_job_fn(chained_job)
            else:
                chained_job = None  # suppressed, no job created

        results.append({
            "triage": triage,
            "message": msg_dict,
            "chained_job": chained_job,
        })

    return results
