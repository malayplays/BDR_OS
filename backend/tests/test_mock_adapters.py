"""test_mock_crm_serves_fixtures + test_mock_email_drafts_inspectable."""

import pytest

from app.adapters.interfaces.types import ActivityWrite, DraftEmail
from app.adapters.mock.crm import MockCRMAdapter
from app.adapters.mock.email import MockEmailAdapter
from app.models.enums import VerdictResult
from app.schemas import Verdict


@pytest.fixture()
def mock_crm():
    return MockCRMAdapter()


@pytest.fixture()
def mock_email():
    return MockEmailAdapter()


@pytest.mark.asyncio
async def test_mock_crm_serves_fixtures(mock_crm):
    accounts = await mock_crm.search_accounts(
        __import__("app.adapters.interfaces.types", fromlist=["AccountQuery"]).AccountQuery()
    )
    assert len(accounts) == 25  # per spec: 25 accounts
    strategic = [a for a in accounts if a.tier == "strategic"]
    assert len(strategic) == 3  # 3 strategic tier for guardrail tests


@pytest.mark.asyncio
async def test_mock_crm_get_account(mock_crm):
    acct = await mock_crm.get_account("acct-001")
    assert acct.ref == "acct-001"


@pytest.mark.asyncio
async def test_mock_crm_get_contacts(mock_crm):
    contacts = await mock_crm.get_contacts("acct-001")
    assert len(contacts) > 0
    assert all(c.account_ref == "acct-001" for c in contacts)


@pytest.mark.asyncio
async def test_mock_crm_write_inspectable(mock_crm):
    v = Verdict(result=VerdictResult.ALLOW)
    aw = ActivityWrite(account_ref="acct-001", activity_type="note", subject="Test")
    ref = await mock_crm.log_activity(v, aw)
    assert ref.startswith("act-")
    assert len(mock_crm.written) == 1
    assert mock_crm.written[0]["type"] == "log_activity"


@pytest.mark.asyncio
async def test_mock_email_drafts_inspectable(mock_email):
    v = Verdict(result=VerdictResult.ALLOW)
    draft = DraftEmail(to=["test@example.com"], subject="Test", body="Hello")
    draft_id = await mock_email.create_draft(v, draft)
    assert draft_id.startswith("draft-")
    assert len(mock_email.drafts) == 1
    assert mock_email.drafts[0]["email"]["to"] == ["test@example.com"]


@pytest.mark.asyncio
async def test_mock_email_list_threads(mock_email):
    from app.adapters.interfaces.types import ThreadQuery
    threads = await mock_email.list_threads(ThreadQuery())
    assert len(threads) == 30  # per spec: 30 email threads
