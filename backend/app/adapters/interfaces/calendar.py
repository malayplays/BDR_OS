from abc import ABC, abstractmethod
from datetime import date

from app.adapters.interfaces.types import (
    CalEvent,
    Capacity,
    EventWrite,
    InviteStatus,
    Slot,
    SlotCriteria,
)
from app.schemas import Verdict


class CalendarAdapter(ABC):
    @abstractmethod
    async def list_events(self, start: date, end: date) -> list[CalEvent]: ...

    @abstractmethod
    async def get_invite_status(self, event_ref: str) -> InviteStatus: ...

    @abstractmethod
    async def find_slots(self, c: SlotCriteria) -> list[Slot]: ...

    @abstractmethod
    async def get_capacity(self, start: date, end: date) -> Capacity: ...

    # writes (policy-gated)
    @abstractmethod
    async def create_event(self, v: Verdict, e: EventWrite) -> str: ...

    @abstractmethod
    async def update_event(self, v: Verdict, event_ref: str, patch: dict) -> str: ...
