"""Access-token claims for runs: the schema, its encoding and the principal it yields.

An access token minted by ``issue_access_token`` carries the run's identity
and authorization facts (``ver: 1``). ``principal_from_claims`` rebuilds from
them exactly the principal ``runtime_auth.load_runtime_principal`` builds
from the database. The claim names a database-validated runtime credential
already carries (``sub``, ``credential_kind``, ``service_id``) keep their
meaning, so an API that validates every token from the database still
accepts these tokens.

``usr`` is zlib-compressed JSON, base64url-encoded: a person's grants are
the bulk of a token, and the same format must fit a browser cookie later.
Tokens are signed, not encrypted; their claims are readable.
"""

from __future__ import annotations

import base64
import json
import logging
import zlib
from typing import Any, Literal
from uuid import UUID

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.config import get_settings
from src.core.principal import UserPrincipal
from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority, RuntimeKind

logger = logging.getLogger(__name__)

CLAIMS_VERSION = 1

# Minting warns, and never refuses, above this encoded size: proxies commonly
# cap a header at 8 KB.
TOKEN_SIZE_WARNING_BYTES = 6144


class _Claims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RoleClaim(_Claims):
    """One role the run user holds; ``bnd`` lists where it applies."""

    id: UUID
    name: str
    perm: list[str]
    bnd: list[tuple[BoundaryKind, UUID | None]]


class BaseRoleClaim(_Claims):
    id: UUID
    perm: list[str]


class UserClaims(_Claims):
    """The run user: identity, home organization and authorization context."""

    id: UUID
    email: str
    name: str | None = None
    kind: str | None = None
    org: UUID | None = None
    ext: bool
    prov: bool
    active: bool
    base: BaseRoleClaim
    roles: list[RoleClaim]


class ActorRoleClaim(_Claims):
    id: UUID
    name: str


class ActorClaims(_Claims):
    """The acting user, when it differs from the run user."""

    id: UUID
    email: str
    name: str | None = None
    org: UUID | None = None
    ext: bool
    prov: bool
    roles: list[ActorRoleClaim]


class PowersClaim(_Claims):
    mode: WorkflowPermissionMode
    grants: list[str]


class RunClaims(_Claims):
    org: UUID | None = None
    wf: UUID | None = None
    sol: UUID | None = None
    sol_active: bool | None = None
    root: UUID | None = None
    by: UUID | None = None
    powers: PowersClaim | None = None


class ImpersonationClaims(_Claims):
    by: UUID
    sid: UUID


def _encode_user(user: UserClaims) -> str:
    raw = json.dumps(user.model_dump(mode="json", exclude_none=True), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).rstrip(b"=").decode("ascii")


def _decode_user(value: str) -> dict[str, Any]:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(padded)))


class AccessTokenClaims(_Claims):
    """The ``ver: 1`` access-token payload for a run."""

    iss: str
    aud: str
    type: Literal["access"] = "access"
    ver: Literal[1] = CLAIMS_VERSION
    iat: int
    exp: int
    jti: str
    # Authority epoch at mint: the decimal string of a signed 64-bit value.
    av: str = Field(pattern=r"^-?[0-9]{1,19}$")
    # The runtime credential id (the execution, service attempt or agent run).
    sub: UUID
    credential_kind: RuntimeKind
    service_id: UUID | None = None
    usr: UserClaims
    act: ActorClaims | None = None
    run: RunClaims
    imp: ImpersonationClaims | None = None

    @field_validator("usr", mode="before")
    @classmethod
    def _decompress_user(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return _decode_user(value)
            except (ValueError, zlib.error) as exc:
                raise ValueError("usr is not a compressed claims block") from exc
        return value

    @field_serializer("usr")
    def _compress_user(self, user: UserClaims) -> str:
        return _encode_user(user)

    def to_payload(self) -> dict[str, Any]:
        """The JWT payload: JSON-safe, absent rather than null fields."""
        return self.model_dump(mode="json", exclude_none=True)


def _encode_claims(claims: AccessTokenClaims) -> str:
    """Sign an access token, warning (never refusing) when it is large."""
    settings = get_settings()
    token = jwt.encode(claims.to_payload(), settings.secret_key, algorithm=settings.algorithm)
    if len(token) > TOKEN_SIZE_WARNING_BYTES:
        logger.warning(
            "authz.token_size_warning: %s access token for %s is %d bytes (warning above %d)",
            claims.credential_kind,
            claims.sub,
            len(token),
            TOKEN_SIZE_WARNING_BYTES,
        )
    return token


def parse_runtime_claims(payload: dict[str, Any]) -> AccessTokenClaims | None:
    """Strictly parse a signature-verified payload as ``ver: 1`` run claims.

    None for anything else: a person's token, a legacy runtime token, or a
    ``ver: 1`` payload that does not match the schema exactly.
    """
    if payload.get("ver") != CLAIMS_VERSION or "credential_kind" not in payload:
        return None
    try:
        claims = AccessTokenClaims.model_validate(payload)
    except ValidationError:
        return None
    if (claims.credential_kind == "service_attempt") != (claims.service_id is not None):
        return None
    return claims


def principal_from_claims(claims: AccessTokenClaims) -> UserPrincipal:
    """The principal ``runtime_auth.load_runtime_principal`` would build.

    Authorization facts come from ``usr`` (the run user). The acting user
    (``act``, or ``usr`` when absent) supplies only identity and the
    SDK-visible role facts, as on the database path.
    """
    user = claims.usr
    context = AuthorizationContext(
        user_id=user.id,
        home_organization_id=user.org,
        base_role_id=user.base.id,
        is_external=user.ext,
        base_permissions=frozenset(user.base.perm),
        role_grants=tuple(
            RoleGrant(
                role_id=role.id,
                permissions=frozenset(role.perm),
                boundaries=tuple(Boundary(kind, organization) for kind, organization in role.bnd),
            )
            for role in user.roles
            if role.bnd
        ),
    )
    run_user = RunUser(user_id=user.id, ctx=context, identity_kind=user.kind, is_active=user.active)

    actor = claims.act
    if actor is None:
        actor_id, email, name, external, provider = user.id, user.email, user.name, user.ext, user.prov
        actor_roles = [(role.id, role.name) for role in user.roles]
    else:
        actor_id, email, name, external, provider = actor.id, actor.email, actor.name, actor.ext, actor.prov
        actor_roles = [(role.id, role.name) for role in actor.roles]
    role_ids = [role_id for role_id, _ in actor_roles]
    role_names = [role_name for _, role_name in actor_roles]

    run = claims.run
    powers = (
        None
        if run.powers is None
        else Powers(
            run.powers.mode,
            tuple(WorkflowGrant(permission=permission) for permission in run.powers.grants),
        )
    )
    imp = claims.imp
    authority = RuntimeAuthority(
        kind=claims.credential_kind,
        credential_id=claims.sub,
        run_user=run_user,
        powers=powers,
        acting_user_id=actor_id,
        organization_id=run.org,
        workflow_id=run.wf,
        started_by_user_id=run.by,
        root_execution_id=run.root,
        service_id=claims.service_id,
        solution_id=run.sol,
        impersonated_by_user_id=None if imp is None else imp.by,
        impersonation_session_id=None if imp is None else imp.sid,
        solution_active=run.sol_active,
    )
    credential = str(claims.sub)
    return UserPrincipal(
        user_id=actor_id,
        email=email,
        name=name or "",
        # As on the database path: runtime authority already established the
        # live run, and an account deactivation does not cancel it.
        is_active=True,
        is_superuser=PLATFORM_ADMIN_ROLE_ID in role_ids,
        is_verified=True,
        is_external=external,
        is_provider_org=provider,
        organization_id=run.org,
        role_ids=role_ids,
        role_names=role_names,
        roles=role_names,
        engine_execution_id=(
            credential if claims.credential_kind in ("workflow_execution", "service_attempt") else None
        ),
        engine_solution_id=None if run.sol is None else str(run.sol),
        service_id=None if claims.service_id is None else str(claims.service_id),
        service_attempt_id=credential if claims.credential_kind == "service_attempt" else None,
        run_user_id=user.id,
        started_by_user_id=run.by,
        root_execution_id=run.root,
        workflow_id=run.wf,
        impersonated_by_user_id=authority.impersonated_by_user_id,
        impersonation_session_id=authority.impersonation_session_id,
        runtime_authority=authority,
    )
