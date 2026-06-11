from abc import ABC, abstractmethod
from datetime import datetime

from app.adapters.interfaces.types import CallMeta, Transcript


class CallRecordingAdapter(ABC):
    @abstractmethod
    async def list_calls(self, since: datetime) -> list[CallMeta]: ...

    @abstractmethod
    async def get_transcript(self, call_ref: str) -> Transcript: ...
