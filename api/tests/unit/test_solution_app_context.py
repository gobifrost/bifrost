"""Human app context may select its own sealed Solution install."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from shared.table_resolution import get_table_or_404
from src.core.principal import UserPrincipal
from src.models.orm.applications import Application
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.solution_scope import (
    SolutionInboundDenied,
    derive_execution_solution_scope,
    file_read_tiers,
    is_authorized_human_app_context,
    resolve_solution_table_by_name,
)


def _caller(
    org_id: UUID,
    *,
    permissions: frozenset[str] = frozenset({"apps.readbasic"}),
) -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="person@example.test",
        organization_id=org_id,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=org_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=permissions,
        ),
    )


async def _install_with_app(db_session, *, app_access: str = "authenticated"):
    org = Organization(id=uuid4(), name=f"Org {uuid4().hex[:8]}", created_by="test")
    solution = Solution(
        id=uuid4(),
        slug=f"solution-{uuid4().hex[:8]}",
        name="Solution",
        organization_id=org.id,
        allow_inbound_access=False,
    )
    app = Application(
        id=uuid4(),
        name="App",
        slug=f"app-{uuid4().hex[:8]}",
        organization_id=org.id,
        solution_id=solution.id,
        repo_path="apps/test",
        access_level=app_access,
    )
    db_session.add(org)
    await db_session.flush()
    db_session.add(solution)
    await db_session.flush()
    db_session.add(app)
    await db_session.flush()
    return org, solution, app


def _context(db_session, caller: Caller, app: Application, solution: Solution):
    return SimpleNamespace(
        db=db_session,
        user=caller.principal,
        org_id=caller.principal.organization_id,
        app_id=str(app.id),
        solution_id=str(solution.id),
        caller_solution_id=None,
        dev_solution_id=None,
    )


@pytest.fixture
def current_caller(monkeypatch):
    from src.services.authorization import enforce

    async def install(caller: Caller) -> Caller:
        monkeypatch.setattr(enforce, "load_caller", AsyncMock(return_value=caller))
        return caller

    return install


async def test_sealed_solution_accepts_visible_human_own_app_context(
    db_session, current_caller
) -> None:
    org, solution, app = await _install_with_app(db_session)
    caller = await current_caller(_caller(org.id))
    ctx = _context(db_session, caller, app, solution)

    assert await is_authorized_human_app_context(db_session, ctx, solution.id)
    assert await derive_execution_solution_scope(
        db_session,
        ctx,
        solution_id=None,
        form_id=None,
        app_id=None,
        target_org_id=org.id,
    ) == solution.id


@pytest.mark.parametrize(
    "case", ["mismatched", "unreadable", "inactive", "missing_basic"]
)
async def test_human_app_context_rejects_nonmatching_unreadable_or_inactive_app(
    db_session, current_caller, case: str
) -> None:
    org, solution, app = await _install_with_app(
        db_session, app_access="private" if case == "unreadable" else "authenticated"
    )
    caller = await current_caller(
        _caller(
            org.id,
            permissions=(
                frozenset()
                if case == "missing_basic"
                else frozenset({"apps.readbasic"})
            ),
        )
    )
    ctx = _context(db_session, caller, app, solution)

    target_id = uuid4() if case == "mismatched" else solution.id
    if case == "inactive":
        solution.status = "inactive"
        await db_session.flush()

    assert not await is_authorized_human_app_context(db_session, ctx, target_id)


async def test_runtime_with_app_header_is_not_promoted_to_own_solution(
    db_session, current_caller
) -> None:
    org, solution, app = await _install_with_app(db_session)
    caller = await current_caller(_caller(org.id))
    caller.principal.runtime_authority = SimpleNamespace(solution_id=solution.id)
    ctx = _context(db_session, caller, app, solution)

    assert not await is_authorized_human_app_context(db_session, ctx, solution.id)


async def test_app_context_cannot_select_another_sealed_install_in_its_own_org(
    db_session, current_caller
) -> None:
    org, first_solution, first_app = await _install_with_app(db_session)
    second_solution = Solution(
        id=uuid4(),
        slug=f"solution-{uuid4().hex[:8]}",
        name="Second Solution",
        organization_id=org.id,
        allow_inbound_access=False,
    )
    db_session.add(second_solution)
    await db_session.flush()
    caller = await current_caller(_caller(org.id))
    header_ctx = _context(db_session, caller, first_app, second_solution)

    with pytest.raises(SolutionInboundDenied):
        await derive_execution_solution_scope(
            db_session,
            header_ctx,
            solution_id=None,
            form_id=None,
            app_id=None,
            target_org_id=org.id,
        )

    header_ctx.app_id = None
    header_ctx.solution_id = None
    with pytest.raises(SolutionInboundDenied):
        await derive_execution_solution_scope(
            db_session,
            header_ctx,
            solution_id=str(second_solution.id),
            form_id=None,
            app_id=str(first_app.id),
            target_org_id=org.id,
        )


async def test_runtime_cannot_promote_with_another_install_app_context(
    db_session, current_caller
) -> None:
    org, first_solution, _ = await _install_with_app(db_session)
    second_solution = Solution(
        id=uuid4(),
        slug=f"solution-{uuid4().hex[:8]}",
        name="Second Solution",
        organization_id=org.id,
        allow_inbound_access=False,
    )
    second_app = Application(
        id=uuid4(),
        name="Second App",
        slug=f"app-{uuid4().hex[:8]}",
        organization_id=org.id,
        solution_id=second_solution.id,
        repo_path="apps/second",
    )
    db_session.add(second_solution)
    await db_session.flush()
    db_session.add(second_app)
    await db_session.flush()
    caller = await current_caller(_caller(org.id))
    caller.principal.runtime_authority = SimpleNamespace(solution_id=first_solution.id)
    ctx = _context(db_session, caller, second_app, second_solution)

    with pytest.raises(SolutionInboundDenied):
        await derive_execution_solution_scope(
            db_session,
            ctx,
            solution_id=None,
            form_id=None,
            app_id=None,
            target_org_id=org.id,
        )

    ctx.app_id = None
    ctx.solution_id = None
    with pytest.raises(SolutionInboundDenied):
        await derive_execution_solution_scope(
            db_session,
            ctx,
            solution_id=str(second_solution.id),
            form_id=None,
            app_id=str(second_app.id),
            target_org_id=org.id,
        )


async def test_legacy_body_app_id_selects_visible_human_sealed_solution(
    db_session, current_caller
) -> None:
    org, solution, app = await _install_with_app(db_session)
    caller = await current_caller(_caller(org.id))
    ctx = _context(db_session, caller, app, solution)
    ctx.app_id = None
    ctx.solution_id = None

    assert await derive_execution_solution_scope(
        db_session,
        ctx,
        solution_id=str(solution.id),
        form_id=None,
        app_id=str(app.id),
        target_org_id=org.id,
    ) == solution.id


async def test_denied_legacy_body_app_target_cannot_fall_back_to_loose_workflow(
    db_session, current_caller
) -> None:
    org, solution, app = await _install_with_app(db_session, app_access="private")
    caller = await current_caller(_caller(org.id))
    ctx = _context(db_session, caller, app, solution)
    ctx.app_id = None
    ctx.solution_id = None

    with pytest.raises(SolutionInboundDenied):
        await derive_execution_solution_scope(
            db_session,
            ctx,
            solution_id=None,
            form_id=None,
            app_id=str(app.id),
            target_org_id=org.id,
        )


async def test_table_name_uuid_and_file_read_use_human_app_context(
    db_session, current_caller
) -> None:
    org, solution, app = await _install_with_app(db_session)
    table = Table(
        id=uuid4(),
        name=f"records_{uuid4().hex[:8]}",
        organization_id=org.id,
        solution_id=solution.id,
        schema={"columns": []},
        access={"policies": []},
    )
    db_session.add(table)
    await db_session.flush()
    caller = await current_caller(_caller(org.id))
    ctx = _context(db_session, caller, app, solution)

    assert await resolve_solution_table_by_name(db_session, ctx, table.name, org.id) == table
    assert await get_table_or_404(ctx, str(table.id)) == table
    assert [tier.name for tier in await file_read_tiers(db_session, ctx, "files", None)] == [
        "solution"
    ]
