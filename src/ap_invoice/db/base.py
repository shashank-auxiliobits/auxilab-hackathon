"""SQLAlchemy declarative base, naming conventions, and common mixins."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names keep Alembic autogenerate diffs stable and make
# migrations portable across environments.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def str_enum(enum_cls: type[enum.Enum], *, length: int) -> SAEnum:
    """Portable VARCHAR-backed enum column that round-trips to the enum on read.

    Stores each member's ``.value`` (not its name), uses a plain VARCHAR (no
    native PG enum, no CHECK constraint — so values can be added without a DDL
    migration), and crucially returns the enum instance on load (a plain String
    column would return ``str`` and silently break ``.value`` access).
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
    )


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key generated application-side (portable & opaque)."""

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Adds created_at / updated_at columns maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
