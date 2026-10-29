"""SDK integration mapping lists follow the caller's Integrations Read reach."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import (
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from shared.sdk_integrations import (
    get_sdk_integration_mapping_dict,
    list_sdk_integration_mappings,
)
from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller
from tests.helpers.authorization import platform_admin_grant


def _ctx(
    *grants: RoleGrant, home: UUID | None, base: frozenset[str] = USER_BASE_PERMISSIONS
) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=base,
        role_grants=grants,
    )


def _person(ctx: AuthorizationContext) -> Caller:
    principal = UserPrincipal(
        user_id=ctx.user_id, email="reach@example.test", organization_id=ctx.home_organization_id
    )
    return Caller(principal, ctx)


def _workflow(ctx: AuthorizationContext, *, mode: str, grants: tuple[str, ...] = ()) -> Caller:
    from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
    from src.services.authorization.explain import Powers, RunUser
    from src.services.authorization.runtime import RuntimeAuthority

    principal = UserPrincipal(
        user_id=uuid4(), email="run@example.test", organization_id=ctx.home_organization_id
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="workflow_execution", credential_id=uuid4(),
        run_user=RunUser(ctx.user_id, ctx, None, is_active=True),
        powers=Powers(
            WorkflowPermissionMode(mode), tuple(WorkflowGrant(permission=p) for p in grants)
        ),
        acting_user_id=principal.user_id, organization_id=ctx.home_organization_id,
        workflow_id=uuid4(), started_by_user_id=ctx.user_id, root_execution_id=uuid4(),
    )
    return Caller(principal, ctx)


def _read_at(boundary: Boundary) -> RoleGrant:
    return RoleGrant(uuid4(), frozenset({"integrations.read"}), (boundary,))


@pytest.fixture
async def world(db_session):
    org_a = Organization(name=f"reach-a-{uuid4().hex[:8]}", is_active=True, created_by="t")
    org_b = Organization(name=f"reach-b-{uuid4().hex[:8]}", is_active=True, created_by="t")
    integration = Integration(name=f"reach-{uuid4().hex[:8]}")
    db_session.add_all([org_a, org_b, integration])
    await db_session.flush()
    for organization_id, entity_id in (
        (org_a.id, "ent-a"),
        (org_b.id, "ent-b"),
        (PROVIDER_ORG_ID, "ent-provider"),
        (None, "ent-global"),
    ):
        db_session.add(
            IntegrationMapping(
                integration_id=integration.id,
                organization_id=organization_id,
                entity_id=entity_id,
            )
        )
    await db_session.flush()
    return {"a": org_a.id, "b": org_b.id, "name": integration.name}


async def _entities(db_session, world, caller: Caller) -> set[str]:
    items = await list_sdk_integration_mappings(db_session, name=world["name"], caller=caller)
    assert items is not None
    return {item["entity_id"] for item in items}


ALL = {"ent-a", "ent-b", "ent-provider", "ent-global"}


@pytest.mark.asyncio
class TestUnscopedListFollowsReach:
    async def test_platform_admin_role_lists_everything(self, db_session, world) -> None:
        caller = _person(_ctx(platform_admin_grant(), home=PROVIDER_ORG_ID))
        assert await _entities(db_session, world, caller) == ALL

    async def test_managed_organizations_exclude_provider_and_global(self, db_session, world) -> None:
        operator = RoleGrant(
            PLATFORM_OPERATOR_ROLE_ID,
            PLATFORM_OPERATOR_PERMISSIONS,
            (Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),),
        )
        caller = _person(_ctx(operator, home=PROVIDER_ORG_ID))
        assert await _entities(db_session, world, caller) == {"ent-a", "ent-b"}

    async def test_organization_boundary_lists_that_subset(self, db_session, world) -> None:
        caller = _person(_ctx(_read_at(Boundary(BoundaryKind.ORGANIZATION, world["b"])), home=world["a"]))
        assert await _entities(db_session, world, caller) == {"ent-b"}

    async def test_home_permission_lists_home_only(self, db_session, world) -> None:
        caller = _person(
            _ctx(home=world["a"], base=USER_BASE_PERMISSIONS | {"integrations.read"})
        )
        assert await _entities(db_session, world, caller) == {"ent-a"}

    async def test_platform_boundary_lists_global_only(self, db_session, world) -> None:
        caller = _person(_ctx(_read_at(Boundary(BoundaryKind.PLATFORM)), home=world["a"]))
        assert await _entities(db_session, world, caller) == {"ent-global"}

    async def test_no_reach_is_refused(self, db_session, world) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await _entities(db_session, world, _person(_ctx(home=world["a"])))
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
class TestWorkflowPowers:
    async def test_full_access_uses_the_run_users_reach(self, db_session, world) -> None:
        caller = _workflow(_ctx(home=world["a"]), mode="full")
        assert await _entities(db_session, world, caller) == {"ent-a", "ent-global"}

    async def test_declared_permissions_without_the_grant_keep_role_reach(self, db_session, world) -> None:
        run_user = _ctx(_read_at(Boundary(BoundaryKind.ORGANIZATION, world["b"])), home=world["a"])
        caller = _workflow(run_user, mode="restricted")
        assert await _entities(db_session, world, caller) == {"ent-b"}

    async def test_declared_permissions_with_the_grant_use_the_run_users_reach(
        self, db_session, world
    ) -> None:
        caller = _workflow(_ctx(home=world["a"]), mode="restricted", grants=("integrations.read",))
        assert await _entities(db_session, world, caller) == {"ent-a", "ent-global"}

    async def test_declared_permissions_with_no_reach_are_refused(self, db_session, world) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await _entities(db_session, world, _workflow(_ctx(home=world["a"]), mode="restricted"))
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
class TestEntityLookupStaysInReach:
    async def test_global_entity_lookup_cannot_find_another_organization(
        self, db_session, world
    ) -> None:
        caller = _workflow(_ctx(home=world["a"]), mode="full")

        async def lookup(entity_id: str):
            return await get_sdk_integration_mapping_dict(
                db_session, name=world["name"], scope="global", caller=caller, entity_id=entity_id
            )

        found = await lookup("ent-a")
        assert found is not None and found["organization_id"] == str(world["a"])
        assert await lookup("ent-b") is None
        assert await lookup("ent-provider") is None
