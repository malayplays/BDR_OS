import json
from datetime import datetime
from pathlib import Path

from app.adapters.interfaces.enrichment import EnrichmentAdapter
from app.adapters.interfaces.types import CompanyProfile, PersonProfile, Signal

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class MockEnrichmentAdapter(EnrichmentAdapter):
    def __init__(self) -> None:
        self._data: dict = {}
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        path = FIXTURES_DIR / "signals.json"
        if path.exists():
            self._data = json.loads(path.read_text())

    async def enrich_company(self, domain: str) -> CompanyProfile:
        for c in self._data.get("companies", []):
            if c["domain"] == domain:
                return CompanyProfile(**c)
        return CompanyProfile(domain=domain, name=domain.split(".")[0].title())

    async def enrich_contact(self, email_or_linkedin: str) -> PersonProfile:
        for p in self._data.get("contacts", []):
            if p.get("email") == email_or_linkedin or p.get("linkedin_url") == email_or_linkedin:
                return PersonProfile(**p)
        return PersonProfile(name="Unknown", title="Unknown", email=email_or_linkedin)

    async def get_signals(self, since: datetime) -> list[Signal]:
        return [Signal(**s) for s in self._data.get("signals", [])]
