from abc import ABC, abstractmethod
from datetime import datetime

from app.adapters.interfaces.types import (
    AutoReplyInfo,
    DraftEmail,
    InboundMessage,
    Thread,
    ThreadQuery,
    ThreadSummary,
)
from app.schemas import Verdict


class EmailAdapter(ABC):
    @abstractmethod
    async def list_threads(self, query: ThreadQuery) -> list[ThreadSummary]: ...

    @abstractmethod
    async def get_thread(self, thread_ref: str) -> Thread: ...

    @abstractmethod
    async def watch_replies(self, since: datetime) -> list[InboundMessage]: ...

    @abstractmethod
    async def detect_autoreply(self, msg: InboundMessage) -> AutoReplyInfo: ...

    # writes (policy-gated)
    @abstractmethod
    async def create_draft(self, v: Verdict, d: DraftEmail) -> str: ...

    @abstractmethod
    async def send(self, v: Verdict, d: DraftEmail) -> str: ...
