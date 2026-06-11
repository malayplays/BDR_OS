import json
import uuid
from datetime import datetime
from pathlib import Path

from app.adapters.interfaces.crm import CRMAdapter
from app.adapters.interfaces.types import (
    Account,
    AccountQuery,
    Activity,
    ActivityWrite,
    Contact,
    CRMTask,
    NormalizedEvent,
    TaskWrite,
)
from app.schemas import Verdict

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class MockCRMAdapter(CRMAdapter):
    def __init__(self) -> None:
        self.written: list[dict] = []
        self._data: dict = {}
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        path = FIXTURES_DIR / "crm.json"
        if path.exists():
            self._data = json.loads(path.read_text())

    async def get_account(self, account_ref: str) -> Account:
        for acct in self._data.get("accounts", []):
            if acct["ref"] == account_ref:
                return Account(**acct)
        raise KeyError(f"Account {account_ref} not found")

    async def search_accounts(self, query: AccountQuery) -> list[Account]:
        results = []
        for acct in self._data.get("accounts", []):
            if query.tier and acct.get("tier") != query.tier:
                continue
            if query.owner and acct.get("owner") != query.owner:
                continue
            results.append(Account(**acct))
        return results

    async def get_contacts(self, account_ref: str) -> list[Contact]:
        return [
            Contact(**c)
            for c in self._data.get("contacts", [])
            if c["account_ref"] == account_ref
        ]

    async def get_open_tasks(self, owner: str) -> list[CRMTask]:
        return [
            CRMTask(**t)
            for t in self._data.get("tasks", [])
            if t.get("status", "open") == "open"
        ]

    async def get_activity(self, account_ref: str, since: datetime) -> list[Activity]:
        return [
            Activity(**a)
            for a in self._data.get("activities", [])
            if a["account_ref"] == account_ref
        ]

    async def pull_events(self, since: datetime) -> list[NormalizedEvent]:
        timeline_path = FIXTURES_DIR / "event_timeline.json"
        if not timeline_path.exists():
            return []
        events = json.loads(timeline_path.read_text())
        return [
            NormalizedEvent(**e)
            for e in events
            if e["occurred_at"] >= since.isoformat()
        ]

    async def log_activity(self, v: Verdict, a: ActivityWrite) -> str:
        ref = f"act-{uuid.uuid4().hex[:8]}"
        self.written.append({"type": "log_activity", "verdict": v.model_dump(), "data": a.model_dump(), "ref": ref})
        return ref

    async def create_task(self, v: Verdict, t: TaskWrite) -> str:
        ref = f"task-{uuid.uuid4().hex[:8]}"
        self.written.append({"type": "create_task", "verdict": v.model_dump(), "data": t.model_dump(), "ref": ref})
        return ref

    async def update_task(self, v: Verdict, task_ref: str, patch: dict) -> None:
        self.written.append({"type": "update_task", "verdict": v.model_dump(), "task_ref": task_ref, "patch": patch})
