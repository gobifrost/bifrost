"""Named App authority retains original-user reach and Global read fallback."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.repositories.applications import ApplicationRepository
from src.routers.applications import get_application_by_id_or_404
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller, allows_permission


def _caller(*, home, grants=(), permissions=("apps.readbasic",)) -> Caller:
    original_user_id = uuid4()
    principal = UserPrincipal(
        user_id=uuid4(),  # effects/transport principal is not authorization context
        email="actor@example.test",
        organization_id=home,
        is_superuser=True,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=original_user_id,
            home_organization_id=home,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(permissions),
            role_grants=grants,
        ),
    )


@pytest.mark.asyncio
async def test_home_apps_readbasic_opens_shared_global_app_without_global_write() -> None:
    home = uuid4()
    caller = _caller(home=home)
    app = SimpleNamespace(id=uuid4(), organization_id=None)
    repo = SimpleNamespace(can_access=AsyncMock(return_value=app))
    ctx = SimpleNamespace(db=object(), org_id=home, user=caller.principal)

    with (
        patch("src.routers.applications.load_caller", new=AsyncMock(return_value=caller)),
        patch("src.routers.applications._application_repository", new=AsyncMock(return_value=repo)),
    ):
        result = await get_application_by_id_or_404(ctx, app.id)

    assert result is app
    assert not allows_permission(caller, "apps.readwrite", None)


@pytest.mark.asyncio
async def test_app_repository_uses_original_reach_not_principal_admin_claim() -> None:
    org_a, org_b = uuid4(), uuid4()
    caller = _caller(
        home=org_a,
        grants=(
            RoleGrant(
                uuid4(),
                frozenset({"apps.readwrite"}),
                (Boundary(BoundaryKind.ORGANIZATION, org_a),),
            ),
        ),
    )
    repo = ApplicationRepository(SimpleNamespace(), org_a, caller=caller)
    allowed = SimpleNamespace(organization_id=org_a, access_level="authenticated")
    blocked = SimpleNamespace(organization_id=org_b, access_level="authenticated")

    assert await repo._can_access_entity(allowed)
    assert not await repo._can_access_entity(blocked)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permission", "access_mode", "allowed"),
    [
        ("apps.readwrite", "read", False),
        ("apps.readwrite", "write", True),
        ("apps.read", "read", False),
        ("apps.read", "inspect", True),
    ],
)
async def test_app_repository_preserves_management_access_without_basic_read(
    permission, access_mode, allowed
) -> None:
    home = uuid4()
    caller = _caller(home=home, permissions={permission})
    app = SimpleNamespace(id=uuid4(), organization_id=home, access_level="role_based")
    repo = ApplicationRepository(SimpleNamespace(), home, caller=caller, access_mode=access_mode)

    assert await repo._can_access_entity(app) is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permissions", "readable", "writable"),
    [
        ({"apps.readbasic"}, True, False),
        ({"apps.readwrite"}, False, True),
        (set(), False, False),
    ],
)
async def test_app_websocket_uses_the_same_read_and_write_decisions(
    db_session, permissions, readable, writable
) -> None:
    from src.models.orm.applications import Application
    from src.models.orm.organizations import Organization
    from src.routers.websocket import can_access_app

    home = uuid4()
    db_session.add(Organization(id=home, name="App Socket Authority", created_by="test"))
    await db_session.flush()
    app = Application(
        name="Socket App",
        slug=f"socket-{uuid4().hex}",
        organization_id=home,
        access_level="everyone",
        created_by="test",
    )
    db_session.add(app)
    await db_session.flush()
    caller = _caller(home=home, permissions=permissions)

    @asynccontextmanager
    async def database():
        yield db_session

    with (
        patch("src.routers.websocket.get_db_context", database),
        patch("src.routers.websocket._current_ws_caller", new=AsyncMock(return_value=caller)),
    ):
        assert await can_access_app(caller.principal, str(app.id)) is readable
        assert await can_access_app(caller.principal, str(app.id), write=True) is writable
