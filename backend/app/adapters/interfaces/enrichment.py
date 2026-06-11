from abc import ABC, abstractmethod
from datetime import datetime

from app.adapters.interfaces.types import CompanyProfile, PersonProfile, Signal


class EnrichmentAdapter(ABC):
    @abstractmethod
    async def enrich_company(self, domain: str) -> CompanyProfile: ...

    @abstractmethod
    async def enrich_contact(self, email_or_linkedin: str) -> PersonProfile: ...

    @abstractmethod
    async def get_signals(self, since: datetime) -> list[Signal]: ...
