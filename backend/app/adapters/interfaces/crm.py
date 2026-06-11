from abc import ABC, abstractmethod
from datetime import datetime

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


class CRMAdapter(ABC):
    # reads
    @abstractmethod
    async def get_account(self, account_ref: str) -> Account: ...

    @abstractmethod
    async def search_accounts(self, query: AccountQuery) -> list[Account]: ...

    @abstractmethod
    async def get_contacts(self, account_ref: str) -> list[Contact]: ...

    @abstractmethod
    async def get_open_tasks(self, owner: str) -> list[CRMTask]: ...

    @abstractmethod
    async def get_activity(self, account_ref: str, since: datetime) -> list[Activity]: ...

    @abstractmethod
    async def pull_events(self, since: datetime) -> list[NormalizedEvent]: ...

    # writes (policy-gated)
    @abstractmethod
    async def log_activity(self, v: Verdict, a: ActivityWrite) -> str: ...

    @abstractmethod
    async def create_task(self, v: Verdict, t: TaskWrite) -> str: ...

    @abstractmethod
    async def update_task(self, v: Verdict, task_ref: str, patch: dict) -> None: ...
