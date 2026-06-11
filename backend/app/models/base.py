import uuid

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
