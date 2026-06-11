import json
import uuid
from datetime import date
from pathlib import Path

from app.adapters.interfaces.calendar import CalendarAdapter
from app.adapters.interfaces.types import (
    CalEvent,
    Capacity,
    EventWrite,
    InviteStatus,
    Slot,
    SlotCriteria,
)
from app.schemas import Verdict

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class MockCalendarAdapter(CalendarAdapter):
    def __init__(self) -> None:
        self.written: list[dict] = []
        self._data: dict = {}
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        path = FIXTURES_DIR / "calendar.json"
        if path.exists():
            self._data = json.loads(path.read_text())

    async def list_events(self, start: date, end: date) -> list[CalEvent]:
        return [CalEvent(**e) for e in self._data.get("events", [])]

    async def get_invite_status(self, event_ref: str) -> InviteStatus:
        for e in self._data.get("events", []):
            if e["ref"] == event_ref:
                return InviteStatus(event_ref=event_ref, attendees=e.get("attendees", []))
        raise KeyError(f"Event {event_ref} not found")

    async def find_slots(self, c: SlotCriteria) -> list[Slot]:
        return [Slot(**s) for s in self._data.get("slots", [])]

    async def get_capacity(self, start: date, end: date) -> Capacity:
        return Capacity(**self._data.get("capacity", {"business_days": 22}))

    async def create_event(self, v: Verdict, e: EventWrite) -> str:
        ref = f"evt-{uuid.uuid4().hex[:8]}"
        self.written.append({"type": "create_event", "verdict": v.model_dump(), "data": e.model_dump(), "ref": ref})
        return ref

    async def update_event(self, v: Verdict, event_ref: str, patch: dict) -> str:
        self.written.append({"type": "update_event", "verdict": v.model_dump(), "event_ref": event_ref, "patch": patch})
        return event_ref
