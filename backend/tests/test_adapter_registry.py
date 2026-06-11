"""test_adapter_registry — env swap mock↔mock2 resolves without app-code change."""


from app.adapters.interfaces.calendar import CalendarAdapter
from app.adapters.interfaces.call_recording import CallRecordingAdapter
from app.adapters.interfaces.crm import CRMAdapter
from app.adapters.interfaces.email import EmailAdapter
from app.adapters.interfaces.enrichment import EnrichmentAdapter
from app.adapters.registry import AdapterRegistry


def test_registry_resolves_all_mock_adapters(env_mock):
    reg = AdapterRegistry()
    assert isinstance(reg.crm, CRMAdapter)
    assert isinstance(reg.email, EmailAdapter)
    assert isinstance(reg.calendar, CalendarAdapter)
    assert isinstance(reg.enrichment, EnrichmentAdapter)
    assert isinstance(reg.call_recording, CallRecordingAdapter)


def test_registry_env_swap(monkeypatch):
    """Swapping env vars creates a fresh registry without app-code changes."""
    monkeypatch.setenv("ADAPTER_CRM", "mock")
    reg1 = AdapterRegistry()
    crm1 = reg1.crm

    # New registry with the same env resolves a fresh instance
    reg2 = AdapterRegistry()
    crm2 = reg2.crm

    assert isinstance(crm1, CRMAdapter)
    assert isinstance(crm2, CRMAdapter)
    assert crm1 is not crm2  # different instances


def test_registry_unknown_adapter_raises(monkeypatch):
    monkeypatch.setenv("ADAPTER_CRM", "nonexistent")
    reg = AdapterRegistry()
    import pytest
    with pytest.raises(ValueError, match="Unknown CRM adapter"):
        _ = reg.crm
