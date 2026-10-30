"""Authority version and token-protocol rollout state.

``authz_versions`` holds the global authority epoch: one row whose random
``version`` changes whenever a committed transaction changes a fact that an
access token carries. Database triggers write it (see
``docs/runbooks/authz-epoch.md``); application code only reads it. Values are
compared for equality only, never ordered.

``authz_protocol_state`` records, durably, the first start of each API
credential protocol. Later migrations use it to refuse upgrades that skip a
rollout step.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class AuthzVersion(Base):
    """One authority version, keyed by subject. Only the global row exists."""

    __tablename__ = "authz_versions"

    subject_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AuthzProtocolState(Base):
    """When an API speaking a credential protocol first started."""

    __tablename__ = "authz_protocol_state"

    protocol: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    first_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
