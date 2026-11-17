"""Identity provider connections, organization domain claims and the
directory state an identity provider keeps in step with Bifrost.

All of these are identity entities (see ``api/src/repositories/README.md``):
they belong to one organization and are looked up by id, never resolved by
name through the org-to-global cascade.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OrganizationDomain(Base):
    """An email domain an organization claims, proven by a DNS TXT record.

    A domain may be claimed (pending) by several organizations but verified
    by only one: the partial unique index enforces it at the database.
    """

    __tablename__ = "organization_domains"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()")
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "domain", name="uq_organization_domains_org_domain"),
        Index(
            "uq_organization_domains_verified_domain",
            "domain",
            unique=True,
            postgresql_where=text("verified_at IS NOT NULL"),
        ),
        CheckConstraint("domain = lower(domain)", name="ck_organization_domains_lowercase"),
    )


class IdentityProvider(Base):
    """One organization's connection to an external directory."""

    __tablename__ = "identity_providers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor: Mapped[str] = mapped_column(String(32), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    provision_as_external: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()"), onupdate=_now
    )

    __table_args__ = (
        Index("ix_identity_providers_organization_id", "organization_id"),
        CheckConstraint(
            "source_type IN ('scim', 'google_directory')",
            name="ck_identity_providers_source_type",
        ),
        CheckConstraint(
            "vendor IN ('entra', 'okta', 'jumpcloud', 'google', 'other')",
            name="ck_identity_providers_vendor",
        ),
    )


class IdentityProviderToken(Base):
    """A bearer credential for a connection. Only the SHA-256 digest is kept;
    the raw token is shown once, when it is generated."""

    __tablename__ = "identity_provider_tokens"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    identity_provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_providers.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # The expiry stage ("expiring" | "expired") already announced with a
    # platform event, so the hourly check fires each stage once.
    expiry_warning_stage: Mapped[str | None] = mapped_column(String(16), default=None)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_identity_provider_tokens_identity_provider_id", "identity_provider_id"),
        CheckConstraint(
            "expiry_warning_stage IN ('expiring', 'expired')",
            name="ck_identity_provider_tokens_expiry_warning_stage",
        ),
    )


class IdentityProviderUser(Base):
    """Links a Bifrost user to the directory entry a connection manages.

    ``id`` is the SCIM resource id handed to the identity provider. A user has
    at most one identity source (``user_id`` is unique).
    """

    __tablename__ = "identity_provider_users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    identity_provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_providers.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    external_id: Mapped[str | None] = mapped_column(String(255), default=None)
    user_name: Mapped[str] = mapped_column(String(320), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The last full resource the provider sent, extensions included.
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # True when this connection created the Bifrost user (vs adopting one).
    created_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The name the provider sent; the access pass copies it to the user.
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    # When a change that grants something (activation, a role, a new name)
    # began waiting for the batched access pass; None when nothing waits.
    # Revocations never wait. See ``identity_provisioning.access_pass``.
    access_pending_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()"), onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint(
            "identity_provider_id", "external_id", name="uq_identity_provider_users_external_id"
        ),
        UniqueConstraint(
            "identity_provider_id", "user_name", name="uq_identity_provider_users_user_name"
        ),
        Index(
            "ix_identity_provider_users_access_pending",
            "access_pending_since",
            postgresql_where=text("access_pending_since IS NOT NULL"),
        ),
    )


class IdentityProviderGroup(Base):
    """A group the identity provider pushed. ``id`` is the SCIM resource id."""

    __tablename__ = "identity_provider_groups"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    identity_provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_providers.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255), default=None)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()"), onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint(
            "identity_provider_id", "external_id", name="uq_identity_provider_groups_external_id"
        ),
        UniqueConstraint(
            "identity_provider_id", "display_name", name="uq_identity_provider_groups_display_name"
        ),
    )


class IdentityProviderGroupMember(Base):
    __tablename__ = "identity_provider_group_members"

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_provider_groups.id", ondelete="CASCADE"), primary_key=True
    )
    identity_provider_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_provider_users.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (
        Index(
            "ix_identity_provider_group_members_user",
            "identity_provider_user_id",
        ),
    )


class IdentityProviderGroupMapping(Base):
    """Marks a group as mapped. A mapping with no roles grants access only."""

    __tablename__ = "identity_provider_group_mappings"

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_provider_groups.id", ondelete="CASCADE"), primary_key=True
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=text("NOW()")
    )


class IdentityProviderGroupRole(Base):
    __tablename__ = "identity_provider_group_roles"

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_provider_group_mappings.group_id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
