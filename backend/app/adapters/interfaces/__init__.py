from app.adapters.interfaces.calendar import CalendarAdapter
from app.adapters.interfaces.call_recording import CallRecordingAdapter
from app.adapters.interfaces.crm import CRMAdapter
from app.adapters.interfaces.email import EmailAdapter
from app.adapters.interfaces.enrichment import EnrichmentAdapter
from app.adapters.interfaces.errors import AdapterError

__all__ = [
    "CRMAdapter",
    "EmailAdapter",
    "CalendarAdapter",
    "EnrichmentAdapter",
    "CallRecordingAdapter",
    "AdapterError",
]
