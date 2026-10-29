"""Authenticated user principal.

Lives in its own fastapi-free module so worker/scheduler-closure code
(repositories, org_filter, agent_executor, MCP tools) can import the
principal without dragging fastapi in via src.core.auth.
tests/unit/test_import_hygiene.py enforces this.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from src.services.authorization.runtime import RuntimeAuthority

@dataclass
class UserPrincipal:
    """
    Authenticated user principal.

    Represents an authenticated user with their identity and permissions.
    Human sessions carry identity claims. Runtime sessions are resolved from
    current durable records. Authorization uses the canonical Caller context;
    actor flags on this object describe the acting user.
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
    is_external: bool = False
    # Acting user's home-organization fact; it grants no permissions or reach.
    is_provider_org: bool = False
    roles: list[str] = field(default_factory=list)
    # Role identity used by table-policy `has_role` evaluator. Populated by
    # `get_execution_context` from the `user_roles` table; empty for token-only
    # principals (e.g. system accounts) and embed sessions.
    role_ids: list[UUID] = field(default_factory=list)
    role_names: list[str] = field(default_factory=list)
    # Internal policy authorities materialize configured policy power. They do
    # not change role membership and are never exposed through ``user`` facts.
    policy_authorities: frozenset[str] = field(default_factory=frozenset)
    embed: bool = False  # True for typed app/form embed session tokens
    embed_kind: str | None = None  # "app" or "form"
    grant: str | None = None  # "hmac" or "public"
    jti: str | None = None  # JWT ID for embed tokens (used for execution scoping)
    app_id: str | None = None  # App ID for embed tokens
    form_id: str | None = None  # Form ID for form embed tokens
    verified_params: dict[str, str] | None = None  # HMAC-verified query params
    verified_context: dict[str, str] | None = None
    capability_fingerprint: str | None = None
    token_exp: int | None = None
    # Browser impersonation is a short-lived, revocable bearer session. These
    # signed claims identify the original operator and its Redis session; the
    # auth boundary revalidates both on every request.
    impersonated_by_user_id: UUID | None = None
    impersonation_session_id: UUID | None = None
    # Engine execution claims (mint_engine_token). Present only on the
    # short-lived execution-scoped engine token: the SIGNED caller install
    # for per-call solution inbound attestation. Ordinary user/admin JWTs
    # carry neither; request-supplied caller ids from those callers are
    # untrusted. None solution_id = _repo/global execution (outside).
    engine_execution_id: str | None = None
    engine_solution_id: str | None = None
    # Service identity claims (mint_service_token). Present only on
    # renewable service-scoped credentials: the definition and attempt the
    # token was minted for. See is_service_principal (solution_scope).
    service_id: str | None = None
    service_attempt_id: str | None = None
    # Run lineage claims (mint_engine_token, and the MCP bridge for agent
    # runs): who the run is for, who started its tree, its first execution
    # and the workflow. Observation and attribution only; never authorization.
    run_user_id: UUID | None = None
    started_by_user_id: UUID | None = None
    root_execution_id: UUID | None = None
    workflow_id: UUID | None = None
    # Verified only by the runtime credential resolver. Transport claims do
    # not populate this field.
    runtime_authority: "RuntimeAuthority | None" = None

    @property
    def is_platform_admin(self) -> bool:
        """Check if user is a platform admin (superuser)."""
        return self.is_superuser

    @property
    def is_system_account(self) -> bool:
        """Check if this is a system account (superuser with no org)."""
        return self.is_superuser and self.organization_id is None
