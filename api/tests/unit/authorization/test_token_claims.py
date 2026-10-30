"""The ``ver: 1`` run claims: schema, compressed ``usr``, size and the principal."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest

from shared.builtin_roles import (
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
    WILDCARD_PERMISSION,
)
from src.config import get_settings
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.services.authorization.context import Boundary, BoundaryKind
from src.services.authorization.tokens import (
    TOKEN_SIZE_WARNING_BYTES,
    AccessTokenClaims,
    ActorClaims,
    ActorRoleClaim,
    BaseRoleClaim,
    ImpersonationClaims,
    PowersClaim,
    RoleClaim,
    RunClaims,
    UserClaims,
    _encode_claims,
    parse_runtime_claims,
    principal_from_claims,
)

_PERMISSIONS = sorted(USER_BASE_PERMISSIONS | PLATFORM_OPERATOR_PERMISSIONS | {"secrets.read", "tables.read"})


def _roles(count: int, boundaries: int, permissions: int) -> list[RoleClaim]:
    """``count`` roles holding ``boundaries`` boundaries and ``permissions``
    permissions between them, shaped like production's largest user."""
    roles: list[RoleClaim] = []
    for index in range(count):
        extra_boundary = index < boundaries - count
        bnd: list[tuple[BoundaryKind, UUID | None]] = [(BoundaryKind.ORGANIZATION, uuid4())]
        if extra_boundary:
            bnd.append((BoundaryKind.MANAGED_ORGANIZATIONS, None))
        share = permissions // count + (1 if index < permissions % count else 0)
        start = (index * 3) % len(_PERMISSIONS)
        perm = [_PERMISSIONS[(start + offset) % len(_PERMISSIONS)] for offset in range(share)]
        roles.append(RoleClaim(id=uuid4(), name=f"Customer Operations Role {index:02d}", perm=perm, bnd=bnd))
    return roles


def _claims(
    *,
    kind: str = "workflow_execution",
    roles: list[RoleClaim] | None = None,
    actor: ActorClaims | None = None,
    imp: ImpersonationClaims | None = None,
    powers: PowersClaim | None = None,
    service_id: UUID | None = None,
    solution: UUID | None = None,
) -> AccessTokenClaims:
    settings = get_settings()
    now = int(time.time())
    user = UserClaims(
        id=uuid4(),
        email="pat.example-user@contoso-managed-services.example",
        name="Pat Example-User",
        kind=None,
        org=uuid4(),
        ext=False,
        prov=False,
        active=True,
        base=BaseRoleClaim(id=USER_ROLE_ID, perm=sorted(USER_BASE_PERMISSIONS)),
        roles=[] if roles is None else roles,
    )
    return AccessTokenClaims(
        iss=settings.jwt_issuer,
        aud=settings.jwt_audience,
        iat=now,
        exp=now + 3600,
        jti=str(uuid4()),
        av=str(-(2**63)),
        sub=uuid4(),
        credential_kind=kind,  # type: ignore[arg-type]
        service_id=service_id,
        usr=user,
        act=actor,
        run=RunClaims(
            org=user.org,
            wf=uuid4(),
            sol=solution,
            sol_active=None if solution is None else True,
            root=uuid4(),
            by=user.id,
            powers=powers,
        ),
        imp=imp,
    )


def _actor(role_count: int) -> ActorClaims:
    return ActorClaims(
        id=uuid4(),
        email="alex.actor-person@contoso-managed-services.example",
        name="Alex Actor-Person",
        org=uuid4(),
        ext=False,
        prov=True,
        roles=[ActorRoleClaim(id=uuid4(), name=f"Provider Technician Role {i:02d}") for i in range(role_count)],
    )


def _largest() -> AccessTokenClaims:
    return _claims(
        roles=_roles(12, 14, 29),
        actor=_actor(12),
        imp=ImpersonationClaims(by=uuid4(), sid=uuid4()),
        powers=PowersClaim(mode=WorkflowPermissionMode.RESTRICTED, grants=_PERMISSIONS[:10]),
    )


def test_claims_round_trip_through_a_signed_token_with_a_compressed_user_block() -> None:
    claims = _largest()
    settings = get_settings()

    payload = jwt.decode(
        _encode_claims(claims), settings.secret_key, algorithms=[settings.algorithm],
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )

    assert isinstance(payload["usr"], str)
    assert payload["ver"] == 1 and payload["type"] == "access"
    assert parse_runtime_claims(payload) == claims


def test_compression_shrinks_the_user_block() -> None:
    claims = _largest()
    plain = json.dumps(claims.usr.model_dump(mode="json", exclude_none=True), separators=(",", ":"))

    assert len(claims.to_payload()["usr"]) < len(plain) * 0.75


def test_the_largest_production_shape_fits_under_the_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        token = _encode_claims(_largest())

    assert len(token) < TOKEN_SIZE_WARNING_BYTES
    assert "authz.token_size_warning" not in caplog.text


def test_an_oversized_token_warns_and_is_still_minted(caplog: pytest.LogCaptureFixture) -> None:
    huge = _claims(roles=_roles(60, 90, 400), actor=_actor(60))

    with caplog.at_level(logging.WARNING):
        token = _encode_claims(huge)

    assert len(token) > TOKEN_SIZE_WARNING_BYTES
    assert "authz.token_size_warning" in caplog.text
    settings = get_settings()
    payload = jwt.decode(
        token, settings.secret_key, algorithms=[settings.algorithm],
        issuer=settings.jwt_issuer, audience=settings.jwt_audience,
    )
    assert parse_runtime_claims(payload) == huge


def _payload(**changes: object) -> dict[str, Any]:
    payload = _claims().to_payload()
    payload.update(changes)
    return payload


_REFUSED: dict[str, Callable[[], dict[str, Any]]] = {
    "extra claim": lambda: _payload(extra="claim"),
    "other version": lambda: _payload(ver=2),
    "not an access token": lambda: _payload(type="refresh"),
    "malformed epoch": lambda: _payload(av="not-a-number"),
    "corrupt usr": lambda: _payload(usr="not-compressed"),
    "unknown kind": lambda: _payload(credential_kind="person"),
    "malformed subject": lambda: _payload(sub="not-a-uuid"),
    "service id on a workflow run": lambda: _payload(service_id=str(uuid4())),
    "service run without service id": lambda: _claims(kind="service_attempt", service_id=uuid4()).to_payload()
    | {"service_id": None},
    "missing run block": lambda: {key: value for key, value in _claims().to_payload().items() if key != "run"},
}


@pytest.mark.parametrize("make_payload", list(_REFUSED.values()), ids=list(_REFUSED))
def test_anything_but_an_exact_v1_run_payload_is_refused(make_payload: Callable[[], dict[str, Any]]) -> None:
    assert parse_runtime_claims(make_payload()) is None


def test_people_and_legacy_runtime_payloads_are_not_run_claims() -> None:
    assert parse_runtime_claims({"sub": str(uuid4()), "email": "p@example.test", "type": "access"}) is None
    assert parse_runtime_claims({"sub": str(uuid4()), "credential_kind": "workflow_execution"}) is None


def test_the_principal_takes_authority_from_the_run_user_and_identity_from_the_actor() -> None:
    roles = [
        RoleClaim(
            id=PLATFORM_ADMIN_ROLE_ID, name="Platform Admin", perm=[WILDCARD_PERMISSION],
            bnd=[(BoundaryKind.PLATFORM, None)],
        ),
        RoleClaim(id=uuid4(), name="Unbounded", perm=["secrets.read"], bnd=[]),
    ]
    actor = _actor(2)
    imp = ImpersonationClaims(by=uuid4(), sid=uuid4())
    powers = PowersClaim(mode=WorkflowPermissionMode.RESTRICTED, grants=["secrets.read"])
    solution = uuid4()
    claims = _claims(roles=roles, actor=actor, imp=imp, powers=powers, solution=solution)

    principal = principal_from_claims(claims)
    authority = principal.runtime_authority

    assert authority is not None
    run_user = authority.run_user
    assert run_user.user_id == claims.usr.id
    assert run_user.is_platform_admin is True
    assert run_user.ctx.base_role_id == USER_ROLE_ID
    assert run_user.ctx.base_permissions == frozenset(USER_BASE_PERMISSIONS)
    # Only a role with a boundary is a grant.
    assert [grant.role_id for grant in run_user.ctx.role_grants] == [PLATFORM_ADMIN_ROLE_ID]
    assert run_user.ctx.role_grants[0].boundaries == (Boundary(BoundaryKind.PLATFORM),)
    # The actor supplies identity and SDK-visible role facts only.
    assert (principal.user_id, principal.email, principal.name) == (actor.id, actor.email, actor.name)
    assert principal.is_superuser is False
    assert principal.is_provider_org is True
    assert principal.role_ids == [role.id for role in actor.roles]
    assert authority.acting_user_id == actor.id
    assert principal.organization_id == claims.run.org
    assert (authority.impersonated_by_user_id, authority.impersonation_session_id) == (imp.by, imp.sid)
    assert principal.impersonation_session_id == imp.sid
    assert authority.powers is not None
    assert authority.powers.mode is WorkflowPermissionMode.RESTRICTED
    assert [grant.permission for grant in authority.powers.grants] == ["secrets.read"]
    assert (authority.solution_id, authority.solution_active) == (solution, True)
    assert principal.engine_solution_id == str(solution)
    assert principal.engine_execution_id == str(claims.sub)
    assert principal.service_attempt_id is None
    assert principal.run_user_id == claims.usr.id


def test_a_run_acting_as_its_own_user_takes_identity_and_roles_from_usr() -> None:
    roles = [
        RoleClaim(
            id=PLATFORM_ADMIN_ROLE_ID, name="Platform Admin", perm=[WILDCARD_PERMISSION],
            bnd=[(BoundaryKind.PLATFORM, None)],
        )
    ]
    claims = _claims(roles=roles)

    principal = principal_from_claims(claims)

    assert principal.user_id == claims.usr.id
    assert principal.is_superuser is True
    assert principal.role_names == ["Platform Admin"]
    assert principal.runtime_authority is not None
    assert principal.runtime_authority.acting_user_id == claims.usr.id


def test_a_service_attempt_principal_carries_its_service_and_attempt() -> None:
    service_id = uuid4()
    claims = _claims(kind="service_attempt", service_id=service_id)

    principal = principal_from_claims(claims)

    assert principal.service_id == str(service_id)
    assert principal.service_attempt_id == str(claims.sub)
    assert principal.engine_execution_id == str(claims.sub)
    assert principal.runtime_authority is not None
    assert principal.runtime_authority.service_id == service_id
