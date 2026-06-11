"""Authenticated user principal.

Lives in its own fastapi-free module so worker/scheduler-closure code
(repositories, org_filter, agent_executor, MCP tools) can import the
principal without dragging fastapi in via src.core.auth.
tests/unit/test_import_hygiene.py enforces this.
"""

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class UserPrincipal:
    """
    Authenticated user principal.

    Represents an authenticated user with their identity and permissions.
    All user info is extracted from JWT claims (no database lookup required).

    Auth model:
    - is_superuser=true, org_id=UUID: Platform admin in an org
    - is_superuser=false, org_id=UUID: Regular org user
    - is_superuser=true, org_id=None: System account (global scope)
    - is_superuser=false, org_id=None: INVALID (rejected at token parsing)
    """
    user_id: UUID
    email: str
    organization_id: UUID | None  # User's org (None for system accounts)
    name: str = ""
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False
    # External (portal/guest) principal. Enforced at the access-level gate
    # only: excluded from access_level="authenticated" entities, admitted by
    # "everyone" and by explicit role grants. Org→global cascade is unchanged.
    # The claim is minted already neutralized for bypass callers (platform
    # admin / provider org) — see shared/external_access.py.
    is_external: bool = False
    roles: list[str] = field(default_factory=list)
    # Role identity used by table-policy `has_role` evaluator. Populated by
    # `get_execution_context` from the `user_roles` table; empty for token-only
    # principals (e.g. system accounts) and embed sessions.
    role_ids: list[UUID] = field(default_factory=list)
    role_names: list[str] = field(default_factory=list)
    embed: bool = False  # True for embed session tokens (scoped to app_id)
    jti: str | None = None  # JWT ID for embed tokens (used for execution scoping)
    app_id: str | None = None  # App ID for embed tokens
    form_id: str | None = None  # Form ID for form embed tokens
    verified_params: dict[str, str] | None = None  # HMAC-verified query params

    @property
    def is_platform_admin(self) -> bool:
        """Check if user is a platform admin (superuser)."""
        return self.is_superuser

    @property
    def is_system_account(self) -> bool:
        """Check if this is a system account (superuser with no org)."""
        return self.is_superuser and self.organization_id is None

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        """Check if user has any of the specified roles."""
        return any(role in self.roles for role in roles)
