"""
User, Role, and UserRole ORM models.

Represents users, roles, and role assignments in the platform.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.builtin_roles import USER_ROLE_ID as _USER_ROLE_ID
from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.agents import Agent
    from src.models.orm.executions import Execution
    from src.models.orm.mfa import MFARecoveryCode, TrustedDevice, UserMFAMethod, UserOAuthAccount, UserPasskey
    from src.models.orm.organizations import Organization


# Identity entity — looked up by ID for auth/audit, not by name with cascade.
# See api/src/repositories/README.md.
class User(Base):
    """User database table."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(255), default=None)
    hashed_password: Mapped[str | None] = mapped_column(String(1024), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_registered: Mapped[bool] = mapped_column(Boolean, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # External (portal/guest) user: an external, non-bypass principal sees only
    # its own org tier — no global (NULL-org) entities and no
    # access_level="authenticated" entitlement. Enforced in OrgScopedRepository.
    is_external: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=text("false")
    )
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )
    mfa_enforced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    # The base role: User or a custom role (never Platform Admin, Platform
    # Operator or Secrets Reader, which are builtin but never base; Platform
    # Admin is held as an additional role). A role that is anyone's base role
    # can't be deleted. Written by `shared.sdk_users.set_user_base_role`.
    base_role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
        default=_USER_ROLE_ID,
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Avatar
    avatar_data: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    avatar_content_type: Mapped[str | None] = mapped_column(String(50), default=None)

    # WebAuthn/Passkeys
    webauthn_user_id: Mapped[bytes | None] = mapped_column(LargeBinary(64), default=None)

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="users")
    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    executions: Mapped[list["Execution"]] = relationship(
        back_populates="executed_by_user", passive_deletes=True
    )
    mfa_methods: Mapped[list["UserMFAMethod"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    recovery_codes: Mapped[list["MFARecoveryCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    trusted_devices: Mapped[list["TrustedDevice"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    oauth_accounts: Mapped[list["UserOAuthAccount"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    passkeys: Mapped[list["UserPasskey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    __table_args__ = (
        Index("ix_users_email", "email"),
        Index("ix_users_organization_id", "organization_id"),
    )


class Role(Base):
    """Role database table.

    Roles are globally defined - org scoping happens at the entity level
    (forms, apps, agents, workflows), not on roles themselves.
    """

    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # The builtin User role is held only through `User.base_role_id`, never
    # assigned through `user_roles`. A custom role can also be someone's base
    # role (it keeps is_base false).
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # A builtin role (Platform Admin, User, Platform Operator, Secrets
    # Reader) has a fixed id (see `shared.builtin_roles`), can't be
    # renamed/deleted, and is hidden from the roles list (unless asked for)
    # and the get/resource-assignment surfaces.
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    users: Mapped[list["UserRole"]] = relationship(
        back_populates="role", passive_deletes="all"
    )
    # Agents via junction table
    agents: Mapped[list["Agent"]] = relationship(
        secondary="agent_roles",
        back_populates="roles",
    )


class UserRole(Base):
    """User-Role association table."""

    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    assigned_by: Mapped[str] = mapped_column(String(255))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="roles")
    role: Mapped["Role"] = relationship(back_populates="users")


class RolePermission(Base):
    """One permission string granted to a role.

    ``permission`` is ``<domain>.<read|readwrite|execute>`` with domain in
    ``src.models.contracts.permissions.PERMISSION_DOMAINS``; the vocabulary
    is validated in the service layer (``src.services.role_permissions``),
    not by a DB enum, so a new domain doesn't require a migration.

    Platform Admin's access is the wildcard permission
    (``shared.builtin_roles.WILDCARD_PERMISSION``), stored as its one row
    here. The permission vocabulary rejects the wildcard, so no custom role
    can hold it.
    """

    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)


class UserRoleBoundary(Base):
    """Where a user's role assignment applies.

    Every `user_roles` row a user holds has one or more boundaries:
    - ``organization``: the assignment applies at one specific org
      (``organization_id`` set).
    - ``managed_organizations``: the assignment applies at every org the
      Platform Operator manages (``organization_id`` NULL).
    - ``platform``: the assignment applies platform-wide, no org boundary
      (``organization_id`` NULL).
    """

    __tablename__ = "user_role_boundaries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    role_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "role_id"],
            ["user_roles.user_id", "user_roles.role_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "kind IN ('organization', 'managed_organizations', 'platform')",
            name="ck_user_role_boundaries_kind",
        ),
        CheckConstraint(
            "(kind = 'organization') = (organization_id IS NOT NULL)",
            name="ck_user_role_boundaries_org_kind_match",
        ),
        # NULLs are distinct in a plain UNIQUE constraint, which would let
        # the same (user_id, role_id, kind) repeat for the two
        # organization_id-less kinds. Two partial unique indexes cover both
        # halves: the org-bound kind (organization_id NOT NULL) can rely on
        # a plain unique index since organization_id is never NULL there;
        # the org-less kinds need a partial index that treats them as one
        # row per (user_id, role_id, kind).
        Index(
            "ux_user_role_boundaries_org",
            "user_id",
            "role_id",
            "kind",
            "organization_id",
            unique=True,
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
        Index(
            "ux_user_role_boundaries_no_org",
            "user_id",
            "role_id",
            "kind",
            unique=True,
            postgresql_where=text("organization_id IS NULL"),
        ),
    )
