import json
from datetime import datetime
from pathlib import Path

from app.adapters.interfaces.call_recording import CallRecordingAdapter
from app.adapters.interfaces.types import CallMeta, Transcript

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class MockCallRecordingAdapter(CallRecordingAdapter):
    def __init__(self) -> None:
        self._data: dict = {}
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        path = FIXTURES_DIR / "transcripts.json"
        if path.exists():
            self._data = json.loads(path.read_text())

    async def list_calls(self, since: datetime) -> list[CallMeta]:
        return [CallMeta(**c) for c in self._data.get("calls", [])]

    async def get_transcript(self, call_ref: str) -> Transcript:
        for t in self._data.get("transcripts", []):
            if t["call_ref"] == call_ref:
                return Transcript(**t)
        raise KeyError(f"Transcript for {call_ref} not found")
