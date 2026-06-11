"""Adapter registry — resolves implementations from environment variables.

Going live = changing one env var + filling keys. Zero app-code changes.
"""

import os

from app.adapters.interfaces.calendar import CalendarAdapter
from app.adapters.interfaces.call_recording import CallRecordingAdapter
from app.adapters.interfaces.crm import CRMAdapter
from app.adapters.interfaces.email import EmailAdapter
from app.adapters.interfaces.enrichment import EnrichmentAdapter


def _get_crm() -> CRMAdapter:
    impl = os.getenv("ADAPTER_CRM", "mock")
    if impl == "mock":
        from app.adapters.mock.crm import MockCRMAdapter
        return MockCRMAdapter()
    raise ValueError(f"Unknown CRM adapter: {impl}")


def _get_email() -> EmailAdapter:
    impl = os.getenv("ADAPTER_EMAIL", "mock")
    if impl == "mock":
        from app.adapters.mock.email import MockEmailAdapter
        return MockEmailAdapter()
    raise ValueError(f"Unknown Email adapter: {impl}")


def _get_calendar() -> CalendarAdapter:
    impl = os.getenv("ADAPTER_CALENDAR", "mock")
    if impl == "mock":
        from app.adapters.mock.calendar import MockCalendarAdapter
        return MockCalendarAdapter()
    raise ValueError(f"Unknown Calendar adapter: {impl}")


def _get_enrichment() -> EnrichmentAdapter:
    impl = os.getenv("ADAPTER_ENRICHMENT", "mock")
    if impl == "mock":
        from app.adapters.mock.enrichment import MockEnrichmentAdapter
        return MockEnrichmentAdapter()
    raise ValueError(f"Unknown Enrichment adapter: {impl}")


def _get_call_recording() -> CallRecordingAdapter:
    impl = os.getenv("ADAPTER_CALLRECORDING", "mock")
    if impl == "mock":
        from app.adapters.mock.call_recording import MockCallRecordingAdapter
        return MockCallRecordingAdapter()
    raise ValueError(f"Unknown CallRecording adapter: {impl}")


class AdapterRegistry:
    """Lazily resolves adapters from env vars."""

    def __init__(self) -> None:
        self._crm: CRMAdapter | None = None
        self._email: EmailAdapter | None = None
        self._calendar: CalendarAdapter | None = None
        self._enrichment: EnrichmentAdapter | None = None
        self._call_recording: CallRecordingAdapter | None = None

    @property
    def crm(self) -> CRMAdapter:
        if self._crm is None:
            self._crm = _get_crm()
        return self._crm

    @property
    def email(self) -> EmailAdapter:
        if self._email is None:
            self._email = _get_email()
        return self._email

    @property
    def calendar(self) -> CalendarAdapter:
        if self._calendar is None:
            self._calendar = _get_calendar()
        return self._calendar

    @property
    def enrichment(self) -> EnrichmentAdapter:
        if self._enrichment is None:
            self._enrichment = _get_enrichment()
        return self._enrichment

    @property
    def call_recording(self) -> CallRecordingAdapter:
        if self._call_recording is None:
            self._call_recording = _get_call_recording()
        return self._call_recording


registry = AdapterRegistry()
