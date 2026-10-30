"""Shared human and runtime access claims, encoding and verified principals.

An access token minted by ``issue_access_token`` carries the run's identity
and authorization facts (``ver: 1``). ``principal_from_claims`` rebuilds from
them exactly the principal ``runtime_auth.load_runtime_principal`` builds
from the database. The claim names a database-validated runtime credential
already carries (``sub``, ``credential_kind``, ``service_id``) keep their
meaning, so an API that validates every token from the database still
accepts these tokens.

Human tokens carry the same envelope and user facts without runtime powers.
``usr`` is zlib-compressed JSON, base64url-encoded: a person's grants are
the bulk of a token. Browser cookies transport these same signed bytes.
Tokens are signed, not encrypted; their claims are readable.

``issue_access_token`` is the one minter. It reads every fact and the
authority epoch in one read-only ``REPEATABLE READ`` snapshot of the primary
database (see ``authz``), so a token's facts and its ``av`` always describe
the same committed state.

Claims in a token minted here are authority while the token is unexpired and
its ``av`` still equals the authority epoch. Runtime tokens also require a live run.
``parse_runtime_claims`` followed by ``authenticate_runtime_token`` is the
only path from such claims to an authenticated principal; it checks the epoch
and the run in one statement on the request's own database session.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, overload
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.config import get_settings
from src.core.database import get_session_factory
from src.core.principal import UserPrincipal
from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole
from src.services.authorization.authz import AuthorityStateMissing, read_authority_state, read_epoch
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant, build_authorization_context
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import (
    RuntimeAuthority,
    RuntimeIdentityError,
    RuntimeKind,
    load_runtime_authority,
)

logger = logging.getLogger(__name__)

CLAIMS_VERSION = 1

# Minting warns, and never refuses, above this encoded size: proxies commonly
# cap a header at 8 KB.
TOKEN_SIZE_WARNING_BYTES = 6144

# Access tokens live 60-90 minutes, picked at random per token, so tokens
# issued together do not all expire and refresh together.
ACCESS_TOKEN_MIN_SECONDS = 3600
ACCESS_TOKEN_SPREAD_SECONDS = 1800

# A run's credential format, recorded on its row (``executions`` /
# ``service_attempts.credential_version``): NULL is the legacy format; this
# value is a claims access token with a run refresh token.
CLAIMS_CREDENTIAL_VERSION = 2

# The lowest route credential protocol (a launcher's, or this build's local
# children's) that accepts claims credentials and refreshes them. Older
# routes receive legacy tokens.
CLAIMS_CREDENTIAL_PROTOCOL = 2


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
    by: UUID | None = None
    sid: UUID | None = None


def _encode_user(user: UserClaims) -> str:
    raw = json.dumps(user.model_dump(mode="json", exclude_none=True), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).rstrip(b"=").decode("ascii")


def _decode_user(value: str) -> dict[str, Any]:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(padded)))


class _AccessClaims(_Claims):
    """The shared signed envelope and compressed authorization facts."""

    iss: str
    aud: str
    type: Literal["access"] = "access"
    ver: Literal[1] = CLAIMS_VERSION
    iat: int
    exp: int
    jti: str
    # Authority epoch at mint: the decimal string of a signed 64-bit value.
    av: str = Field(pattern=r"^-?[0-9]{1,19}$")
    sub: UUID
    usr: UserClaims

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


class AccessTokenClaims(_AccessClaims):
    """The ``ver: 1`` access-token payload for a run."""

    credential_kind: RuntimeKind
    service_id: UUID | None = None
    act: ActorClaims | None = None
    run: RunClaims
    imp: ImpersonationClaims | None = None


class HumanAccessTokenClaims(_AccessClaims):
    """Human access facts; no runtime powers or capability fields."""

    sid: UUID | None = None


class RunRefreshClaims(_Claims):
    """A run refresh token: identity only, no expiry; valid while the run is
    live, and accepted only by ``/auth/refresh``."""

    iss: str
    aud: str
    type: Literal["run_refresh"] = "run_refresh"
    ver: Literal[1] = CLAIMS_VERSION
    iat: int
    jti: str
    sub: UUID
    credential_kind: Literal["workflow_execution", "service_attempt"]
    service_id: UUID | None = None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def _encode_claims(claims: AccessTokenClaims | HumanAccessTokenClaims | RunRefreshClaims) -> str:
    """Sign a run token, warning (never refusing) when an access token is large."""
    settings = get_settings()
    token = jwt.encode(claims.to_payload(), settings.secret_key, algorithm=settings.algorithm)
    if isinstance(claims, _AccessClaims) and len(token) > TOKEN_SIZE_WARNING_BYTES:
        logger.warning(
            "authz.token_size_warning: %s access token for %s is %d bytes (warning above %d)",
            claims.credential_kind if isinstance(claims, AccessTokenClaims) else "human",
            claims.sub,
            len(token),
            TOKEN_SIZE_WARNING_BYTES,
        )
    return token


def parse_run_refresh_claims(payload: dict[str, Any]) -> RunRefreshClaims | None:
    """Strictly parse a signature-verified payload as a run refresh token."""
    if payload.get("type") != "run_refresh" or payload.get("ver") != CLAIMS_VERSION:
        return None
    try:
        claims = RunRefreshClaims.model_validate(payload)
    except ValidationError:
        return None
    if (claims.credential_kind == "service_attempt") != (claims.service_id is not None):
        return None
    return claims


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


def parse_human_claims(payload: dict[str, Any]) -> HumanAccessTokenClaims | None:
    """Parse only an exact, signature-verified human access payload."""
    if payload.get("ver") != CLAIMS_VERSION:
        return None
    try:
        claims = HumanAccessTokenClaims.model_validate(payload)
    except ValidationError:
        return None
    if claims.sub != claims.usr.id or claims.usr.kind is not None or not claims.usr.active:
        return None
    if claims.usr.org is None and not _context_from_user(claims.usr).is_platform_admin:
        return None
    return claims


def _context_from_user(user: UserClaims) -> AuthorizationContext:
    return AuthorizationContext(
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


def _human_principal(claims: HumanAccessTokenClaims) -> UserPrincipal:
    user = claims.usr
    context = _context_from_user(user)
    names = [role.name for role in user.roles]
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        name=user.name or "",
        organization_id=user.org,
        is_active=user.active,
        is_verified=True,
        is_superuser=context.is_platform_admin,
        is_external=user.ext,
        is_provider_org=user.prov,
        roles=["authenticated", *names],
        role_ids=[role.id for role in user.roles],
        role_names=names,
        authorization_context=context,
        auth_session_id=claims.sid,
        token_exp=claims.exp,
    )


def principal_from_claims(claims: AccessTokenClaims) -> UserPrincipal:
    """The principal ``runtime_auth.load_runtime_principal`` would build.

    Authorization facts come from ``usr`` (the run user). The acting user
    (``act``, or ``usr`` when absent) supplies only identity and the
    SDK-visible role facts, as on the database path.
    """
    user = claims.usr
    context = _context_from_user(user)
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


def attribution_principal(claims: AccessTokenClaims) -> UserPrincipal:
    """The principal a signature-verified token names, for attribution only.

    The request middleware labels audit and usage records with it before
    authentication has run, without a database session. It is never
    authority: the authentication dependency still validates the token with
    ``authenticate_runtime_token`` before any route handler runs, and a
    refused token never reaches one.
    """
    return principal_from_claims(claims)


RuntimeRefusal = Literal["authority_state_missing", "not_live", "refresh_required"]


@dataclass(frozen=True)
class RuntimeAuthResult:
    """The validator's decision: a principal, or why the token was refused."""

    principal: UserPrincipal | None
    refusal: RuntimeRefusal | None = None

    @property
    def refresh_required(self) -> bool:
        """Whether a refresh would make the token's run usable again."""
        return self.refusal == "refresh_required"


async def authenticate_runtime_token(db: AsyncSession, claims: AccessTokenClaims) -> RuntimeAuthResult:
    """Validate ``ver: 1`` runtime claims in one statement on ``db``.

    The statement reads the global authority epoch and the run's liveness
    (execution status, agent run status, or service attempt state, lease and
    service) on the primary, in the caller's transaction:

    - a missing epoch row refuses and logs an error; it is never a default;
    - a run that is not live refuses without a challenge, because a refresh
      would be refused too;
    - an epoch that differs from ``av`` refuses with ``refresh_required``:
      a fact the token carries may have changed since it was minted;
    - otherwise the claims are the principal.
    """
    state = await read_authority_state(
        db,
        claims.credential_kind,
        claims.sub,
        service_id=claims.service_id,
        now=datetime.now(timezone.utc),
    )
    if state.epoch is None:
        logger.error("authz.epoch_missing: the global authority epoch row is missing; refusing a runtime token")
        return RuntimeAuthResult(None, "authority_state_missing")
    if not state.live:
        return RuntimeAuthResult(None, "not_live")
    if state.epoch != int(claims.av):
        return RuntimeAuthResult(None, "refresh_required")
    return RuntimeAuthResult(principal_from_claims(claims))


async def authenticate_human_token(db: AsyncSession, claims: HumanAccessTokenClaims) -> RuntimeAuthResult:
    """Authenticate human facts with one primary epoch lookup, no role cache."""
    try:
        epoch = await read_epoch(db)
    except AuthorityStateMissing:
        logger.error("authz.epoch_missing: refusing a human access token")
        return RuntimeAuthResult(None, "authority_state_missing")
    if int(claims.av) != epoch:
        return RuntimeAuthResult(None, "refresh_required")
    return RuntimeAuthResult(_human_principal(claims))


# --- The minter ----------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeSubject:
    """The run a token is for."""

    kind: RuntimeKind
    credential_id: UUID
    service_id: UUID | None = None


@dataclass(frozen=True)
class HumanSubject:
    """A database human, optionally bound to a durable refresh session."""

    user_id: UUID
    session_id: UUID | None = None


@dataclass(frozen=True)
class IssuedAccessToken:
    token: str
    expires_at: datetime
    claims: AccessTokenClaims


@dataclass(frozen=True)
class IssuedHumanAccessToken:
    token: str
    expires_at: datetime
    claims: HumanAccessTokenClaims


class TokenSubjectUnavailable(Exception):
    """The run is not live or its records no longer establish authority."""


def _now() -> int:
    return int(time.time())


@overload
async def issue_access_token(subject: RuntimeSubject) -> IssuedAccessToken:
    """Mint a credential for an authorized live runtime."""


@overload
async def issue_access_token(subject: HumanSubject) -> IssuedHumanAccessToken:
    """Mint a credential for an eligible human and durable session family."""


async def issue_access_token(subject: RuntimeSubject | HumanSubject) -> IssuedAccessToken | IssuedHumanAccessToken:
    """Mint a ``ver: 1`` access token for a live run or eligible human.

    Every fact and the epoch come from one read-only ``REPEATABLE READ``
    transaction on the primary, set before its first statement: read-only
    snapshot transactions take no locks, never wait and never fail to
    serialize, so there is nothing to retry. A writer that commits after the
    snapshot installs a new epoch, which refuses this token from then on.

    Raises ``TokenSubjectUnavailable`` when the run cannot be authorized and
    ``AuthorityStateMissing`` when the epoch row is missing.
    """
    async with get_session_factory()() as db:
        await db.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
        claims = (
            await _snapshot_human_claims(db, subject)
            if isinstance(subject, HumanSubject)
            else await _snapshot_claims(db, subject)
        )
    token = _encode_claims(claims)
    expires_at = datetime.fromtimestamp(claims.exp, timezone.utc)
    if isinstance(claims, HumanAccessTokenClaims):
        return IssuedHumanAccessToken(token, expires_at, claims)
    return IssuedAccessToken(token, expires_at, claims)


async def _snapshot_human_claims(db: AsyncSession, subject: HumanSubject) -> HumanAccessTokenClaims:
    row = (
        await db.execute(
            select(
                User.id, User.email, User.name, User.organization_id,
                User.is_external, User.identity_kind, User.is_active, User.is_system, Organization.is_provider,
            )
            .outerjoin(Organization, Organization.id == User.organization_id)
            .where(User.id == subject.user_id)
        )
    ).one_or_none()
    if row is None or not row.is_active or row.is_system or row.identity_kind is not None:
        raise TokenSubjectUnavailable("The human account is not available.")
    context = await build_authorization_context(db, subject.user_id)
    if context.home_organization_id is None and not context.is_platform_admin:
        raise TokenSubjectUnavailable("A human account without an organization must be a Platform Admin.")
    held_roles = (
        await db.execute(
            select(Role.id, Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == subject.user_id)
            .order_by(Role.name, Role.id)
        )
    ).all()
    permissions: dict[UUID, list[str]] = {role.id: [] for role in held_roles}
    if permissions:
        for role_id, permission in (
            await db.execute(
                select(RolePermission.role_id, RolePermission.permission)
                .where(RolePermission.role_id.in_(permissions))
            )
        ).all():
            permissions[role_id].append(permission)
    boundaries = {grant.role_id: grant.boundaries for grant in context.role_grants}
    settings = get_settings()
    now = _now()
    return HumanAccessTokenClaims(
        iss=settings.jwt_issuer, aud=settings.jwt_audience, iat=now,
        exp=now + ACCESS_TOKEN_MIN_SECONDS + secrets.randbelow(ACCESS_TOKEN_SPREAD_SECONDS + 1),
        jti=str(uuid4()), av=str(await read_epoch(db)), sub=row.id, sid=subject.session_id,
        usr=UserClaims(
            id=row.id, email=row.email, name=row.name, kind=row.identity_kind,
            org=row.organization_id, ext=bool(row.is_external), prov=bool(row.is_provider),
            active=row.is_active,
            base=BaseRoleClaim(id=context.base_role_id, perm=sorted(context.base_permissions)),
            roles=[RoleClaim(
                id=role.id, name=role.name, perm=sorted(permissions[role.id]),
                bnd=[(b.kind, b.organization_id) for b in boundaries.get(role.id, ())],
            ) for role in held_roles],
        ),
    )


def issue_run_refresh_token(subject: RuntimeSubject) -> str:
    """A run refresh token for a workflow execution or service attempt.

    It carries identity only and reads no database: ``/auth/refresh``
    re-evaluates the run when it is redeemed. Agent runs have none; their
    tokens never leave the process.
    """
    if subject.kind == "agent_run":
        raise ValueError("Agent runs do not receive run refresh tokens.")
    settings = get_settings()
    return _encode_claims(
        RunRefreshClaims(
            iss=settings.jwt_issuer,
            aud=settings.jwt_audience,
            iat=_now(),
            jti=str(uuid4()),
            sub=subject.credential_id,
            credential_kind=subject.kind,
            service_id=subject.service_id,
        )
    )


async def redeem_run_refresh_token(payload: dict[str, Any]) -> IssuedAccessToken:
    """A new access token for the run a verified run refresh payload names.

    The run is re-evaluated exactly as at first mint; a run that is no longer
    live, or a payload that is not an exact run refresh token, raises
    ``TokenSubjectUnavailable``.
    """
    claims = parse_run_refresh_claims(payload)
    if claims is None:
        raise TokenSubjectUnavailable("Not a run refresh token.")
    return await issue_access_token(RuntimeSubject(claims.credential_kind, claims.sub, claims.service_id))


async def _snapshot_claims(db: AsyncSession, subject: RuntimeSubject) -> AccessTokenClaims:
    try:
        authority = await load_runtime_authority(
            db, subject.kind, subject.credential_id, service_id=subject.service_id
        )
    except RuntimeIdentityError as exc:
        raise TokenSubjectUnavailable(str(exc)) from exc

    run_user = authority.run_user
    user_ids = {run_user.user_id, authority.acting_user_id}
    people = {
        row.id: row
        for row in (
            await db.execute(
                select(
                    User.id,
                    User.email,
                    User.name,
                    User.organization_id,
                    User.is_external,
                    Organization.is_provider,
                )
                .outerjoin(Organization, Organization.id == User.organization_id)
                .where(User.id.in_(user_ids))
            )
        ).all()
    }
    if user_ids - people.keys():
        raise TokenSubjectUnavailable("A run user or acting user no longer exists.")

    held_roles: dict[UUID, list[tuple[UUID, str]]] = {user_id: [] for user_id in user_ids}
    for user_id, role_id, role_name in (
        await db.execute(
            select(UserRole.user_id, Role.id, Role.name)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids))
        )
    ).all():
        held_roles[user_id].append((role_id, role_name))
    for roles in held_roles.values():
        roles.sort(key=lambda role: (role[1], str(role[0])))

    run_user_roles = held_roles[run_user.user_id]
    permissions: dict[UUID, list[str]] = {role_id: [] for role_id, _ in run_user_roles}
    if permissions:
        for role_id, permission in (
            await db.execute(
                select(RolePermission.role_id, RolePermission.permission).where(
                    RolePermission.role_id.in_(permissions)
                )
            )
        ).all():
            permissions[role_id].append(permission)

    try:
        epoch = await read_epoch(db)
    except AuthorityStateMissing:
        logger.error("authz.epoch_missing: the global authority epoch row is missing; refusing to mint")
        raise

    boundaries = {grant.role_id: grant.boundaries for grant in run_user.ctx.role_grants}
    user_row = people[run_user.user_id]
    usr = UserClaims(
        id=run_user.user_id,
        email=user_row.email,
        name=user_row.name,
        kind=run_user.identity_kind,
        org=run_user.ctx.home_organization_id,
        ext=run_user.ctx.is_external,
        prov=bool(user_row.is_provider),
        active=run_user.is_active,
        base=BaseRoleClaim(id=run_user.ctx.base_role_id, perm=sorted(run_user.ctx.base_permissions)),
        roles=[
            RoleClaim(
                id=role_id,
                name=role_name,
                perm=sorted(permissions[role_id]),
                bnd=[(boundary.kind, boundary.organization_id) for boundary in boundaries.get(role_id, ())],
            )
            for role_id, role_name in run_user_roles
        ],
    )
    act = None
    if authority.acting_user_id != run_user.user_id:
        actor_row = people[authority.acting_user_id]
        act = ActorClaims(
            id=actor_row.id,
            email=actor_row.email,
            name=actor_row.name,
            org=actor_row.organization_id,
            ext=actor_row.is_external,
            prov=bool(actor_row.is_provider),
            roles=[
                ActorRoleClaim(id=role_id, name=role_name)
                for role_id, role_name in held_roles[authority.acting_user_id]
            ],
        )
    powers = authority.powers
    settings = get_settings()
    issued_at = _now()
    return AccessTokenClaims(
        iss=settings.jwt_issuer,
        aud=settings.jwt_audience,
        iat=issued_at,
        exp=issued_at + ACCESS_TOKEN_MIN_SECONDS + secrets.randbelow(ACCESS_TOKEN_SPREAD_SECONDS + 1),
        jti=str(uuid4()),
        av=str(epoch),
        sub=authority.credential_id,
        credential_kind=authority.kind,
        service_id=authority.service_id if authority.kind == "service_attempt" else None,
        usr=usr,
        act=act,
        run=RunClaims(
            org=authority.organization_id,
            wf=authority.workflow_id,
            sol=authority.solution_id,
            sol_active=authority.solution_active,
            root=authority.root_execution_id,
            by=authority.started_by_user_id,
            powers=(
                None
                if powers is None
                else PowersClaim(mode=powers.mode, grants=sorted(grant.permission for grant in powers.grants))
            ),
        ),
        imp=(
            None
            if authority.impersonated_by_user_id is None and authority.impersonation_session_id is None
            else ImpersonationClaims(by=authority.impersonated_by_user_id, sid=authority.impersonation_session_id)
        ),
    )


# --- Run credentials ---------------------------------------------------------------


@dataclass(frozen=True)
class RunCredentials:
    """The credentials a run's process starts with.

    ``refresh_token`` is None for the legacy format, whose refresh slot holds
    the access token itself; ``credential_version`` is what the run's row
    records.
    """

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    credential_version: int | None


def credential_version_for(protocol: int) -> int | None:
    """The credential format a route of this protocol receives."""
    return CLAIMS_CREDENTIAL_VERSION if protocol >= CLAIMS_CREDENTIAL_PROTOCOL else None


async def issue_run_credentials(
    subject: RuntimeSubject, *, protocol: int, legacy_lifetime_seconds: int
) -> RunCredentials:
    """Credentials for a run about to start on a route of ``protocol``.

    Routes that speak the claims protocol get a claims access token and a run
    refresh token. Older routes (remote launchers that do not report it) get
    exactly today's legacy token, valid for ``legacy_lifetime_seconds`` and
    accepted through the bridge while the run is live.
    """
    if credential_version_for(protocol) is None:
        token, expires_at = _legacy_runtime_token(subject, lifetime_seconds=legacy_lifetime_seconds)
        return RunCredentials(token, None, expires_at, None)
    issued = await issue_access_token(subject)
    return RunCredentials(
        issued.token, issue_run_refresh_token(subject), issued.expires_at, CLAIMS_CREDENTIAL_VERSION
    )


# --- Legacy runtime tokens (the bridge; token PR1c deletes this section) ------------

# Lifetimes of the legacy format, unchanged: an execution's timeout plus five
# minutes (a day for workflows without a timeout); services are re-minted on
# every lease heartbeat.
LEGACY_NO_TIMEOUT_TOKEN_SECONDS = 86_400
LEGACY_SERVICE_TOKEN_SECONDS = 900


def legacy_engine_token_lifetime(timeout_seconds: int) -> int:
    """A legacy execution token's lifetime for a workflow timeout (0 = none)."""
    effective = timeout_seconds if timeout_seconds > 0 else LEGACY_NO_TIMEOUT_TOKEN_SECONDS
    return effective + 300


def _legacy_runtime_token(subject: RuntimeSubject, *, lifetime_seconds: int) -> tuple[str, datetime]:
    """A runtime token in the legacy format: the credential's identity only.

    Only for routes that do not speak the claims protocol, and for the
    rotation of legacy-format service attempts. The API validates it from
    the database on every request (the bridge), so it authorizes only while
    its run is live.
    """
    claims: dict[str, Any] = {"sub": str(subject.credential_id), "credential_kind": subject.kind}
    if subject.service_id is not None:
        claims["service_id"] = str(subject.service_id)
    return _encode_legacy(claims, lifetime_seconds)


def _encode_legacy(claims: dict[str, Any], lifetime_seconds: int) -> tuple[str, datetime]:
    """Sign a legacy runtime token exactly as ``create_access_token`` did."""
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds)
    payload = {
        **claims,
        "exp": expires_at,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm), expires_at
