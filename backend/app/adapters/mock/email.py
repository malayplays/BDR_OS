import json
import uuid
from datetime import datetime
from pathlib import Path

from app.adapters.interfaces.email import EmailAdapter
from app.adapters.interfaces.types import (
    AutoReplyInfo,
    DraftEmail,
    InboundMessage,
    Thread,
    ThreadQuery,
    ThreadSummary,
)
from app.schemas import Verdict

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class MockEmailAdapter(EmailAdapter):
    def __init__(self) -> None:
        self.drafts: list[dict] = []
        self.sent: list[dict] = []
        self._data: dict = {}
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        path = FIXTURES_DIR / "threads.json"
        if path.exists():
            self._data = json.loads(path.read_text())

    async def list_threads(self, query: ThreadQuery) -> list[ThreadSummary]:
        return [ThreadSummary(**t) for t in self._data.get("thread_summaries", [])]

    async def get_thread(self, thread_ref: str) -> Thread:
        for t in self._data.get("threads", []):
            if t["ref"] == thread_ref:
                return Thread(**t)
        raise KeyError(f"Thread {thread_ref} not found")

    async def watch_replies(self, since: datetime) -> list[InboundMessage]:
        return [InboundMessage(**m) for m in self._data.get("inbound_messages", [])]

    async def detect_autoreply(self, msg: InboundMessage) -> AutoReplyInfo:
        body_lower = msg.body.lower()
        if "out of office" in body_lower or "ooo" in body_lower:
            return AutoReplyInfo(is_autoreply=True, kind="ooo")
        if "undeliverable" in body_lower or "bounce" in body_lower:
            return AutoReplyInfo(is_autoreply=True, kind="bounce")
        return AutoReplyInfo(is_autoreply=False)

    async def create_draft(self, v: Verdict, d: DraftEmail) -> str:
        draft_id = f"draft-{uuid.uuid4().hex[:8]}"
        self.drafts.append({"id": draft_id, "verdict": v.model_dump(), "email": d.model_dump()})
        return draft_id

    async def send(self, v: Verdict, d: DraftEmail) -> str:
        msg_id = f"sent-{uuid.uuid4().hex[:8]}"
        self.sent.append({"id": msg_id, "verdict": v.model_dump(), "email": d.model_dump()})
        return msg_id
