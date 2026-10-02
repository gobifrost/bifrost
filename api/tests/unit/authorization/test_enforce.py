"""The R3a enforcement helper (``src.services.authorization.enforce``)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from shared.builtin_roles import (
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)
from src.core.constants import PROVIDER_ORG_ID, SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.contracts.access_list import CurrentGate
from src.services.access_list import ACCESS_LIST
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import (
    EVERYTHING,
    NARROWER_PERMISSIONS,
    Caller,
    decide_for,
    entry_for_operation,
    held_permissions_by_user,
    load_caller,
    operation_key,
    operation_reach,
    org_target,
    permitted_organizations,
    privileged_user_ids,
    require_operation,
    require_unprotected,
)
from src.services.authorization.evaluator import GLOBAL, HOME, TargetKind, cross_org, decide

ORG_A = UUID("00000000-0000-0000-0000-00000000a001")
ORG_B = UUID("00000000-0000-0000-0000-00000000a002")

EVALUATOR_ENTRIES = [e for e in ACCESS_LIST if e.current_gate == CurrentGate.EVALUATOR]


def _principal(**kwargs) -> UserPrincipal:
    kwargs.setdefault("user_id", uuid4())
    kwargs.setdefault("email", "caller@example.com")
    kwargs.setdefault("organization_id", ORG_A)
    return UserPrincipal(**kwargs)


def _ctx(*grants: RoleGrant, home: UUID | None = ORG_A, base: UUID = USER_ROLE_ID) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=base,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS if base == USER_ROLE_ID else frozenset(),
        role_grants=grants,
    )


def _person(ctx: AuthorizationContext, **principal_kwargs) -> Caller:
    return Caller(_principal(**principal_kwargs), ctx)


def _operator() -> Caller:
    return _person(
        _ctx(
            RoleGrant(
                PLATFORM_OPERATOR_ROLE_ID,
                PLATFORM_OPERATOR_PERMISSIONS,
                (Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),),
            ),
            home=PROVIDER_ORG_ID,
        ),
        organization_id=PROVIDER_ORG_ID,
    )


def _engine() -> Caller:
    return Caller(
        _principal(
            user_id=SYSTEM_USER_UUID,
            organization_id=None,
            is_superuser=True,
            engine_execution_id=str(uuid4()),
        ),
        None,
    )


def _service() -> Caller:
    return Caller(
        _principal(service_id=str(uuid4()), engine_execution_id=str(uuid4())),
        None,
    )


TARGETS = (HOME, cross_org(ORG_A), cross_org(ORG_B), cross_org(PROVIDER_ORG_ID), GLOBAL)


class TestExecutionCredentials:
    """Engine and service tokens are decided by ``is_superuser``, exactly as
    the superuser dependency decided them."""

    @pytest.mark.parametrize("entry", EVALUATOR_ENTRIES, ids=operation_key)
    def test_engine_token_is_allowed_everywhere(self, entry) -> None:
        for target in TARGETS:
            decision = decide_for(_engine(), entry, target)
            assert decision.allowed, (target, decision.rule)
            assert decision.rule == "execution_credential:superuser"

    @pytest.mark.parametrize("entry", EVALUATOR_ENTRIES, ids=operation_key)
    def test_service_token_is_refused_everywhere(self, entry) -> None:
        for target in TARGETS:
            assert not decide_for(_service(), entry, target).allowed

    def test_engine_token_reaches_every_org_and_is_an_admin(self) -> None:
        assert permitted_organizations(_engine(), "users.read") == EVERYTHING
        assert _engine().is_platform_admin

    def test_service_token_reaches_nowhere(self) -> None:
        assert permitted_organizations(_service(), "users.read").is_empty
        assert not _service().is_platform_admin
        with pytest.raises(HTTPException) as exc_info:
            operation_reach(_service(), "users.list")
        assert exc_info.value.status_code == 403

    def test_embed_session_is_refused(self) -> None:
        embed = Caller(_principal(embed=True, is_superuser=True), None)
        assert not decide_for(embed, entry_for_operation("users.list"), HOME).allowed
        assert not embed.is_platform_admin


@pytest.mark.asyncio
class TestPlatformAdminFromTheDatabase:
    async def _user(self, db_session, base_role_id: UUID):
        from src.models import User as UserORM
        from src.models.orm.organizations import Organization

        org = Organization(name=f"enf-{uuid4().hex[:8]}", is_active=True, created_by="t")
        db_session.add(org)
        await db_session.flush()
        user = UserORM(email=f"enf-{uuid4().hex[:8]}@t.local", organization_id=org.id)
        db_session.add(user)
        await db_session.flush()
        user.base_role_id = base_role_id
        await db_session.flush()
        return user

    async def test_a_superuser_token_for_a_demoted_user_is_not_an_admin(self, db_session) -> None:
        user = await self._user(db_session, USER_ROLE_ID)
        caller = await load_caller(
            db_session, _principal(user_id=user.id, organization_id=user.organization_id, is_superuser=True)
        )
        assert not caller.is_platform_admin
        with pytest.raises(HTTPException) as exc_info:
            require_operation(caller, "users.list", HOME)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "You don't have permission to view users"

    async def test_the_stored_base_role_makes_the_admin(self, db_session) -> None:
        user = await self._user(db_session, PLATFORM_ADMIN_ROLE_ID)
        caller = await load_caller(
            db_session, _principal(user_id=user.id, organization_id=user.organization_id)
        )
        assert caller.is_platform_admin
        require_operation(caller, "users.list", cross_org(ORG_B))

    async def test_a_token_for_a_deleted_user_is_refused(self, db_session) -> None:
        caller = await load_caller(db_session, _principal())
        assert caller.ctx is None
        assert not decide_for(caller, entry_for_operation("users.list"), HOME).allowed


class TestTargets:
    def test_org_target(self) -> None:
        assert org_target(None) == GLOBAL
        target = org_target(ORG_B)
        assert (target.kind, target.organization_id) == (TargetKind.CROSS, ORG_B)

    def test_own_org_is_home(self) -> None:
        grant = RoleGrant(uuid4(), frozenset({"users.read"}), (Boundary(BoundaryKind.ORGANIZATION, ORG_A),))
        caller = _person(_ctx(grant))
        assert decide_for(caller, entry_for_operation("users.get"), org_target(ORG_A)).allowed
        assert not decide_for(caller, entry_for_operation("users.get"), org_target(ORG_B)).allowed

    def test_platform_entries_need_a_platform_boundary(self) -> None:
        at_org = RoleGrant(uuid4(), frozenset({"roles.read"}), (Boundary(BoundaryKind.ORGANIZATION, ORG_A),))
        at_platform = RoleGrant(uuid4(), frozenset({"roles.read"}), (Boundary(BoundaryKind.PLATFORM),))
        entry = entry_for_operation("roles.list")
        assert not decide_for(_person(_ctx(at_org)), entry, GLOBAL).allowed
        assert decide_for(_person(_ctx(at_platform)), entry, GLOBAL).allowed


class TestOperations:
    def test_every_evaluator_entry_has_a_unique_key(self) -> None:
        keys = [operation_key(e) for e in EVALUATOR_ENTRIES]
        assert len(keys) == len(set(keys))
        for key in keys:
            assert entry_for_operation(key).current_gate == CurrentGate.EVALUATOR

    def test_narrower_permissions_are_accepted_and_others_refused(self) -> None:
        for operation, permissions in NARROWER_PERMISSIONS.items():
            for permission in permissions:
                assert entry_for_operation(operation, permission).permission == permission
        with pytest.raises(ValueError):
            entry_for_operation("users.update", "roles.readwrite")

    def test_a_non_evaluator_operation_is_unknown(self) -> None:
        with pytest.raises(KeyError):
            entry_for_operation("workflows.list")


def _contexts() -> list[AuthorizationContext]:
    perm = "users.read"
    return [
        _ctx(),
        _ctx(base=PLATFORM_ADMIN_ROLE_ID, home=PROVIDER_ORG_ID),
        _operator().ctx,  # type: ignore[list-item]
        _ctx(RoleGrant(uuid4(), frozenset({perm}), (Boundary(BoundaryKind.ORGANIZATION, ORG_B),))),
        _ctx(RoleGrant(uuid4(), frozenset({perm}), (Boundary(BoundaryKind.PLATFORM),))),
        _ctx(
            RoleGrant(uuid4(), frozenset({perm}), (Boundary(BoundaryKind.ORGANIZATION, ORG_A),)),
            RoleGrant(uuid4(), frozenset({"users.readwrite"}), (Boundary(BoundaryKind.MANAGED_ORGANIZATIONS),)),
        ),
    ]


class TestPermittedOrganizations:
    """List filtering must agree with ``decide`` on every row's target."""

    @pytest.mark.parametrize("ctx", _contexts())
    def test_reach_matches_decide(self, ctx) -> None:
        caller = Caller(_principal(), ctx)
        entry = entry_for_operation("users.get")
        reach = permitted_organizations(caller, "users.read")
        for org in (ORG_A, ORG_B, PROVIDER_ORG_ID, uuid4(), None):
            assert reach.covers(org) == decide(ctx, entry, org_target(org)).allowed, org

    def test_managed_never_covers_global_or_provider(self) -> None:
        reach = permitted_organizations(_operator(), "users.read")
        assert reach.managed and not reach.include_global
        assert not reach.covers(None)
        assert not reach.covers(PROVIDER_ORG_ID)
        assert reach.covers(ORG_A)

    def test_platform_boundary_covers_global_only(self) -> None:
        grant = RoleGrant(uuid4(), frozenset({"users.read"}), (Boundary(BoundaryKind.PLATFORM),))
        reach = permitted_organizations(_person(_ctx(grant, home=ORG_B)), "users.read")
        assert reach.covers(None)
        assert not reach.covers(ORG_A)

    def test_no_reach_is_refused_before_lookup(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            operation_reach(_person(_ctx()), "users.list")
        assert exc_info.value.status_code == 403


class TestProtectedTargets:
    def test_only_an_admin_changes_a_privileged_user(self) -> None:
        admin = _person(_ctx(base=PLATFORM_ADMIN_ROLE_ID))
        require_unprotected(admin, True)
        require_unprotected(_operator(), False)
        with pytest.raises(HTTPException) as exc_info:
            require_unprotected(_operator(), True)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
class TestPrivilegedUsers:
    async def _seed(self, db_session):
        from src.models import Role, RolePermission, User as UserORM, UserRole
        from src.models.orm.organizations import Organization

        org = Organization(name=f"priv-{uuid4().hex[:8]}", is_active=True, created_by="t")
        support, config, empty = (Role(name=f"r-{uuid4().hex[:6]}", created_by="t") for _ in range(3))
        db_session.add_all([org, support, config, empty])
        await db_session.flush()
        db_session.add_all(
            [
                RolePermission(role_id=support.id, permission="users.read"),
                RolePermission(role_id=config.id, permission="configs.readwrite"),
            ]
        )
        users = {}
        for name in ("plain", "stitched", "support_only", "admin"):
            users[name] = UserORM(email=f"{name}-{uuid4().hex[:6]}@t.local", organization_id=org.id)
            db_session.add(users[name])
        await db_session.flush()
        users["admin"].base_role_id = PLATFORM_ADMIN_ROLE_ID
        db_session.add_all(
            [
                UserRole(user_id=users["plain"].id, role_id=empty.id, assigned_by="t"),
                UserRole(user_id=users["stitched"].id, role_id=support.id, assigned_by="t"),
                UserRole(user_id=users["stitched"].id, role_id=config.id, assigned_by="t"),
                UserRole(user_id=users["support_only"].id, role_id=support.id, assigned_by="t"),
            ]
        )
        await db_session.flush()
        return users

    async def test_stitched_custom_roles_and_admins_are_privileged(self, db_session) -> None:
        users = await self._seed(db_session)
        privileged = await privileged_user_ids(db_session, [u.id for u in users.values()])
        assert privileged == {users["stitched"].id, users["admin"].id}

    async def test_a_fixed_number_of_queries(self, db_session) -> None:
        users = await self._seed(db_session)
        statements: list[str] = []

        def count(conn, cursor, statement, *args) -> None:
            statements.append(statement)

        engine = db_session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", count)
        try:
            held = await held_permissions_by_user(db_session, [u.id for u in users.values()])
        finally:
            event.remove(engine, "before_cursor_execute", count)
        assert len(statements) == 3
        assert held[users["support_only"].id] == USER_BASE_PERMISSIONS | {"users.read"}

    async def test_no_users(self, db_session) -> None:
        assert await held_permissions_by_user(db_session, []) == {}
