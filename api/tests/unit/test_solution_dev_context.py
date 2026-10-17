"""Solution local-dev context is a short-lived, server-validated capability."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

import shared.solution_dev as solution_dev
from shared.sdk_users import set_platform_admin
from src.core import auth
from src.core.principal import UserPrincipal
from src.core.security import decode_token
from src.models import User as UserORM
from src.models.orm.applications import Application
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.solution_scope import check_inbound_allowed, resolve_trustworthy_caller
from src.services.solutions.deploy import solution_entity_id
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller


def _request(*, headers: dict[str, str] | None = None, query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/sdk/tables",
            "query_string": query,
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
            ],
        }
    )


async def _admin(db_session, *, stored_admin: bool, token_admin: bool = False) -> UserPrincipal:
    org = Organization(name=f"dev-context-{uuid4().hex[:8]}", is_active=True, created_by="test")
    db_session.add(org)
    await db_session.flush()
    user = UserORM(email=f"dev-context-{uuid4().hex[:8]}@example.test", organization_id=org.id)
    db_session.add(user)
    await db_session.flush()
    await set_platform_admin(db_session, user, stored_admin, assigned_by="test")
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=org.id,
        is_superuser=token_admin,
    )


async def _active_solution(db_session, organization_id):
    solution = Solution(
        id=uuid4(),
        slug=f"dev-{uuid4().hex[:8]}",
        name="Dev Context",
        organization_id=organization_id,
    )
    db_session.add(solution)
    await db_session.flush()
    return solution


@pytest.mark.asyncio
async def test_issues_non_bearer_context_for_canonical_platform_admin(db_session) -> None:
    principal = await _admin(db_session, stored_admin=True)
    solution = await _active_solution(db_session, principal.organization_id)
    manifest_app_id = uuid4()

    response = await solution_dev.issue_solution_dev_context(
        db_session, principal, solution.id, manifest_app_id
    )

    claims = decode_token(response.token, expected_type="solution_dev")
    assert claims is not None
    assert claims["sub"] == str(principal.user_id)
    assert claims["solution_id"] == str(solution.id)
    assert claims["organization_id"] == str(solution.organization_id)
    assert claims["app_id"] == str(solution_entity_id(solution.id, manifest_app_id))
    assert decode_token(response.token, expected_type="access") is None
    assert response.expires_at > 0


@pytest.mark.asyncio
async def test_claim_only_superuser_cannot_issue_context(db_session) -> None:
    principal = await _admin(db_session, stored_admin=False, token_admin=True)
    solution = await _active_solution(db_session, principal.organization_id)

    with pytest.raises(HTTPException) as raised:
        await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, uuid4())

    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_solutions_write_holder_can_issue_context_without_platform_admin(
    db_session, monkeypatch
) -> None:
    principal = await _admin(db_session, stored_admin=False)
    solution = await _active_solution(db_session, principal.organization_id)
    caller = Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=principal.organization_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(
                RoleGrant(
                    uuid4(),
                    frozenset({"solutions.readwrite"}),
                    (Boundary(BoundaryKind.ORGANIZATION, principal.organization_id),),
                ),
            ),
        ),
    )
    monkeypatch.setattr(solution_dev, "load_caller", AsyncMock(return_value=caller))

    response = await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, uuid4())

    assert response.solution_id == solution.id


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["embed", "engine_execution_id", "service_id"])
async def test_non_human_credentials_cannot_issue_development_context(db_session, credential) -> None:
    principal = await _admin(db_session, stored_admin=True, token_admin=True)
    solution = await _active_solution(db_session, principal.organization_id)
    setattr(principal, credential, True if credential == "embed" else str(uuid4()))
    with pytest.raises(HTTPException) as raised:
        await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, uuid4())
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_runtime_authority_cannot_issue_development_context(db_session) -> None:
    principal = await _admin(db_session, stored_admin=True)
    solution = await _active_solution(db_session, principal.organization_id)
    # A typed execution credential may carry Full workflow powers, but local
    # solution development is an unelevated human-only admission path.
    principal.runtime_authority = object()

    with pytest.raises(HTTPException) as raised:
        await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, uuid4())

    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_issue_refuses_inactive_or_external_human_after_token_mint(db_session) -> None:
    principal = await _admin(db_session, stored_admin=True)
    solution = await _active_solution(db_session, principal.organization_id)
    user = await db_session.get(UserORM, principal.user_id)
    assert user is not None

    user.is_active = False
    await db_session.flush()
    with pytest.raises(HTTPException) as raised:
        await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, uuid4())
    assert raised.value.status_code == 403

    user.is_active = True
    user.is_external = True
    await db_session.flush()
    with pytest.raises(HTTPException) as raised:
        await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, uuid4())
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_foreign_portable_app_id_remaps_but_own_captured_id_stays_direct(db_session) -> None:
    principal = await _admin(db_session, stored_admin=True)
    target = await _active_solution(db_session, principal.organization_id)
    source = await _active_solution(db_session, principal.organization_id)
    source_app_id = uuid4()
    db_session.add(
        Application(
            id=source_app_id,
            name="Source",
            slug=f"source-{uuid4().hex[:8]}",
            solution_id=source.id,
        )
    )
    own_captured_id = uuid4()
    db_session.add(
        Application(
            id=own_captured_id,
            name="Captured",
            slug=f"captured-{uuid4().hex[:8]}",
            solution_id=target.id,
        )
    )
    await db_session.flush()

    portable = await solution_dev.issue_solution_dev_context(db_session, principal, target.id, source_app_id)
    captured = await solution_dev.issue_solution_dev_context(db_session, principal, target.id, own_captured_id)

    assert portable.app_id == solution_entity_id(target.id, source_app_id)
    assert captured.app_id == own_captured_id


@pytest.mark.asyncio
async def test_context_header_validates_subject_signature_expiry_and_membership(db_session, monkeypatch) -> None:
    principal = await _admin(db_session, stored_admin=True)
    other_admin = await _admin(db_session, stored_admin=True)
    solution = await _active_solution(db_session, principal.organization_id)
    manifest_app_id = uuid4()
    issued = await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, manifest_app_id)
    monkeypatch.setattr(auth, "get_user_roles", AsyncMock(return_value=([], [])))

    valid_request = _request(headers={solution_dev.DEV_CONTEXT_HEADER: issued.token})
    ctx = await auth.get_execution_context(valid_request, principal, db_session)
    assert ctx.dev_solution_id == str(solution.id)
    assert ctx.solution_id == str(solution.id)
    assert ctx.app_id == str(solution_entity_id(solution.id, manifest_app_id))
    assert principal.policy_authorities == frozenset()

    signature = issued.token.rsplit(".", 1)[1]
    forged = issued.token.rsplit(".", 1)[0] + "." + (
        "a" if signature[0] != "a" else "b"
    ) + signature[1:]
    invalid_tokens = [
        forged,
        (await solution_dev.issue_solution_dev_context(
            db_session, principal, solution.id, manifest_app_id, expires_delta=timedelta(seconds=-1)
        )).token,
    ]
    for token in invalid_tokens:
        with pytest.raises(HTTPException) as raised:
            await auth.get_execution_context(_request(headers={solution_dev.DEV_CONTEXT_HEADER: token}), principal, db_session)
        assert raised.value.status_code == 401
        assert raised.value.detail == "Invalid solution dev context"

    with pytest.raises(HTTPException) as raised:
        await auth.get_execution_context(_request(headers={solution_dev.DEV_CONTEXT_HEADER: issued.token}), other_admin, db_session)
    assert raised.value.status_code == 403
    assert raised.value.detail == "Invalid solution dev context"

    with pytest.raises(HTTPException) as raised:
        await auth.get_execution_context(
            _request(headers={solution_dev.DEV_CONTEXT_HEADER: issued.token, "X-Bifrost-App": str(uuid4())}),
            principal,
            db_session,
        )
    assert raised.value.status_code == 403
    assert raised.value.detail == "Invalid solution dev context"

    other_solution = await _active_solution(db_session, principal.organization_id)
    app_id = solution_entity_id(solution.id, manifest_app_id)
    db_session.add(Application(id=app_id, name="A", slug=f"a-{uuid4().hex[:8]}", solution_id=other_solution.id))
    await db_session.flush()
    with pytest.raises(HTTPException) as raised:
        await auth.get_execution_context(valid_request, principal, db_session)
    assert raised.value.status_code == 403
    assert raised.value.detail == "Invalid solution dev context"


@pytest.mark.asyncio
async def test_context_revalidates_canonical_admin_install_and_cross_install_ingress(db_session, monkeypatch) -> None:
    principal = await _admin(db_session, stored_admin=True)
    # Revocation must leave another active human Platform Admin in place.
    await _admin(db_session, stored_admin=True)
    solution = await _active_solution(db_session, principal.organization_id)
    target = await _active_solution(db_session, principal.organization_id)
    target.allow_inbound_access = False
    issued = await solution_dev.issue_solution_dev_context(db_session, principal, solution.id, uuid4())
    monkeypatch.setattr(auth, "get_user_roles", AsyncMock(return_value=([], [])))

    cross_request = _request(
        headers={solution_dev.DEV_CONTEXT_HEADER: issued.token},
        query=f"solution={target.id}&caller_solution={target.id}".encode(),
    )
    ctx = await auth.get_execution_context(cross_request, principal, db_session)
    assert ctx.solution_id == str(target.id)
    assert ctx.dev_solution_id == str(solution.id)
    assert await resolve_trustworthy_caller(db_session, ctx) == solution.id
    assert not await check_inbound_allowed(db_session, target.id, solution.id)

    original_org = solution.organization_id
    solution.organization_id = None
    await db_session.flush()
    with pytest.raises(HTTPException) as moved:
        await auth.get_execution_context(_request(headers={solution_dev.DEV_CONTEXT_HEADER: issued.token}), principal, db_session)
    assert moved.value.status_code == 403
    solution.organization_id = original_org
    await db_session.flush()

    await set_platform_admin(
        db_session,
        await db_session.get(UserORM, principal.user_id),
        False,
        assigned_by="test",
    )
    with pytest.raises(HTTPException) as raised:
        await auth.get_execution_context(_request(headers={solution_dev.DEV_CONTEXT_HEADER: issued.token}), principal, db_session)
    assert raised.value.status_code == 403
    assert raised.value.detail == "Invalid solution dev context"

    await set_platform_admin(
        db_session,
        await db_session.get(UserORM, principal.user_id),
        True,
        assigned_by="test",
    )
    solution.status = "inactive"
    await db_session.flush()
    with pytest.raises(HTTPException) as raised:
        await auth.get_execution_context(_request(headers={solution_dev.DEV_CONTEXT_HEADER: issued.token}), principal, db_session)
    assert raised.value.status_code == 403
    assert raised.value.detail == "Invalid solution dev context"
