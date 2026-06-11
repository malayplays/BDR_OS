
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def env_mock(monkeypatch):
    """Set standard mock env for adapter registry tests."""
    monkeypatch.setenv("ADAPTER_CRM", "mock")
    monkeypatch.setenv("ADAPTER_EMAIL", "mock")
    monkeypatch.setenv("ADAPTER_CALENDAR", "mock")
    monkeypatch.setenv("ADAPTER_ENRICHMENT", "mock")
    monkeypatch.setenv("ADAPTER_CALLRECORDING", "mock")
    monkeypatch.setenv("DRAFT_ONLY_UNTIL", "2099-12-31")
    monkeypatch.setenv("MAX_NEW_OUTBOUND_PER_DAY", "40")
    monkeypatch.setenv("STRATEGIC_ACCOUNTS", '["acct-001", "acct-002", "acct-003"]')
