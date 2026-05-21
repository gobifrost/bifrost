"""
MFA-related ORM models.

Represents MFA methods, recovery codes, trusted devices, and OAuth accounts.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import MFAMethodStatus, MFAMethodType
from src.models.orm.base import Base



class UserMFAMethod(Base):
    """User MFA method enrollment."""

    __tablename__ = "user_mfa_methods"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    method_type: Mapped[MFAMethodType] = mapped_column(
        sqlalchemy.Enum(
            MFAMethodType,
            name="mfa_method_type",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    )
    status: Mapped[MFAMethodStatus] = mapped_column(
        sqlalchemy.Enum(
            MFAMethodStatus,
            name="mfa_method_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=MFAMethodStatus.PENDING,
    )
    encrypted_secret: Mapped[str | None] = mapped_column(Text, default=None)
    mfa_metadata: Mapped[dict] = mapped_column(JSONB, default={})
    last_used_counter: Mapped[int | None] = mapped_column(Integer, default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="mfa_methods")

    __table_args__ = (
        Index("ix_user_mfa_methods_user_id", "user_id"),
        Index("ix_user_mfa_methods_user_status", "user_id", "status"),
    )


class MFARecoveryCode(Base):
    """MFA recovery codes."""

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(255))
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    used_from_ip: Mapped[str | None] = mapped_column(String(45), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="recovery_codes")

    __table_args__ = (
        Index("ix_mfa_recovery_codes_user_id", "user_id"),
        Index("ix_mfa_recovery_codes_user_unused", "user_id", "is_used"),
    )


class TrustedDevice(Base):
    """Trusted devices that can bypass MFA."""

    __tablename__ = "trusted_devices"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    device_fingerprint: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str | None] = mapped_column(String(255), default=None)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_ip_address: Mapped[str | None] = mapped_column(String(45), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="trusted_devices")

    __table_args__ = (
        Index("ix_trusted_devices_user_id", "user_id"),
        Index(
            "ix_trusted_devices_fingerprint",
            "user_id",
            "device_fingerprint",
            unique=True,
        ),
    )


class UserOAuthAccount(Base):
    """Links OAuth accounts to users for SSO."""

    __tablename__ = "user_oauth_accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider_id: Mapped[str] = mapped_column(String(50))
    provider_user_id: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="oauth_accounts")

    __table_args__ = (
        Index(
            "ix_user_oauth_provider_user",
            "provider_id",
            "provider_user_id",
            unique=True,
        ),
        Index("ix_user_oauth_user", "user_id"),
    )


class UserPasskey(Base):
    """WebAuthn passkey credentials for passwordless authentication."""

    __tablename__ = "user_passkeys"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # WebAuthn credential data (required for verification)
    credential_id: Mapped[bytes] = mapped_column(sqlalchemy.LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(sqlalchemy.LargeBinary)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)

    # Credential metadata
    transports: Mapped[list] = mapped_column(JSONB, default=[])  # usb, nfc, ble, internal
    device_type: Mapped[str] = mapped_column(String(50))  # singleDevice, multiDevice
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)

    # User-facing info
    name: Mapped[str] = mapped_column(String(255))  # "MacBook Pro Touch ID"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="passkeys")

    __table_args__ = (
        Index("ix_user_passkeys_user_id", "user_id"),
        Index("ix_user_passkeys_credential_id", "credential_id", unique=True),
    )
