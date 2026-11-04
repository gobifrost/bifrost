"""Inbound public OAuth client metadata. Registration grants no account access."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class OAuthClientRegistration(Base):
    __tablename__ = "oauth_client_registrations"

    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    client_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
