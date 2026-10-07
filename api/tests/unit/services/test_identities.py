"""The identities service: what each identity counts as in use, who may change
it, and the order and wording the API returns."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from shared.identities import ensure_default_identity
from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.models.contracts.identities import IdentityCreate, IdentityPublic, IdentityUpdate
from src.models.enums import IdentityKind
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller
from src.services.identities import (
    DEFAULT_DELETE_MESSAGE,
    DEFAULT_RENAME_MESSAGE,
    IdentityError,
    create_identity,
    delete_identity,
    list_identities,
    list_run_identities,
    rename_identity,
    require_delegation,
)
from tests.helpers.authorization import admin_caller

pytestmark = pytest.mark.asyncio


def _lifecycle_holder(*organization_ids: UUID) -> Caller:
    """Holds users.read and users.lifecycle.readwrite at each organization, and nothing else."""
    ctx = AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=PROVIDER_ORG_ID,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=(
            RoleGrant(
                uuid4(),
                frozenset({"users.read", "users.lifecycle.readwrite"}),
                tuple(Boundary(BoundaryKind.ORGANIZATION, org_id) for org_id in organization_ids),
            ),
        ),
    )
    return Caller(UserPrincipal(user_id=ctx.user_id, email="holder@example.com", organization_id=PROVIDER_ORG_ID), ctx)


async def _organization(db_session, name: str):
    from src.models.orm.organizations import Organization

    organization = Organization(name=f"{name}-{uuid4().hex[:6]}", is_active=True, created_by="t")
    db_session.add(organization)
    await db_session.flush()
    await ensure_default_identity(db_session, organization)
    return organization


async def _workflow(
    db_session,
    organization_id: UUID | None,
    *,
    name: str,
    run_identity_id: UUID | None = None,
    display_name: str | None = None,
):
    from src.models.orm.workflows import Workflow

    workflow = Workflow(
        name=name,
        display_name=display_name,
        function_name=name,
        path=f"workflows/{name}.py",
        organization_id=organization_id,
        run_identity_id=run_identity_id,
    )
    db_session.add(workflow)
    await db_session.flush()
    return workflow


async def _by_kind(db_session, caller: Caller, organization_id: UUID, kind: str) -> IdentityPublic:
    (found,) = [
        identity
        for identity in await list_identities(db_session, caller, organization_id=organization_id)
        if identity.identity_kind == kind
    ]
    return found


class TestWorkflowsUsing:
    async def test_default_counts_unnamed_workflows_of_its_organization_and_custom_counts_only_named(
        self, db_session
    ) -> None:
        admin = admin_caller()
        contoso, fabrikam = await _organization(db_session, "Contoso"), await _organization(db_session, "Fabrikam")
        custom = await create_identity(db_session, admin, IdentityCreate(name="Nightly", organization_id=contoso.id))
        await _workflow(db_session, contoso.id, name=f"unnamed_a_{uuid4().hex[:6]}")
        await _workflow(db_session, contoso.id, name=f"unnamed_b_{uuid4().hex[:6]}")
        await _workflow(db_session, contoso.id, name=f"named_{uuid4().hex[:6]}", run_identity_id=custom.id)
        await _workflow(db_session, fabrikam.id, name=f"elsewhere_{uuid4().hex[:6]}")

        contoso_default = await _by_kind(db_session, admin, contoso.id, "org_default")
        fabrikam_default = await _by_kind(db_session, admin, fabrikam.id, "org_default")
        listed_custom = await _by_kind(db_session, admin, contoso.id, "custom")
        assert (contoso_default.workflows_using, fabrikam_default.workflows_using) == (2, 1)
        assert listed_custom.workflows_using == 1

    async def test_global_default_counts_unnamed_global_workflows(self, db_session) -> None:
        admin = admin_caller()
        before = (await list_identities(db_session, admin))[0]
        assert before.identity_kind == IdentityKind.GLOBAL_DEFAULT
        await _workflow(db_session, None, name=f"global_{uuid4().hex[:6]}")

        after = (await list_identities(db_session, admin))[0]
        assert after.workflows_using == before.workflows_using + 1


class TestList:
    async def test_global_first_then_organizations_by_name(self, db_session) -> None:
        admin = admin_caller()
        zeta, alpha = await _organization(db_session, "Zeta"), await _organization(db_session, "Alpha")
        await create_identity(db_session, admin, IdentityCreate(name="Shared", organization_id=None))

        listed = await list_identities(db_session, admin)
        assert listed[0].identity_kind == IdentityKind.GLOBAL_DEFAULT
        global_rows = [i for i in listed if i.organization_id is None]
        assert listed[: len(global_rows)] == global_rows
        assert [i.identity_kind for i in global_rows][:2] == ["global_default", "custom"]
        names = [i.organization_name for i in listed if i.organization_id in (zeta.id, alpha.id)]
        assert names == sorted(names)

    async def test_a_holder_lists_only_where_they_hold_read(self, db_session) -> None:
        contoso, fabrikam = await _organization(db_session, "Contoso"), await _organization(db_session, "Fabrikam")
        holder = _lifecycle_holder(contoso.id)

        listed = await list_identities(db_session, holder)
        assert {i.organization_id for i in listed} == {contoso.id}
        with pytest.raises(Exception) as refused:
            await list_identities(db_session, holder, organization_id=fabrikam.id)
        assert getattr(refused.value, "status_code", None) == 403


class TestChange:
    async def test_create_is_decided_at_the_organization_and_global_needs_platform(self, db_session) -> None:
        contoso, fabrikam = await _organization(db_session, "Contoso"), await _organization(db_session, "Fabrikam")
        holder = _lifecycle_holder(contoso.id)

        created = await create_identity(db_session, holder, IdentityCreate(name="Held", organization_id=contoso.id))
        assert (created.identity_kind, created.organization_id) == ("custom", contoso.id)
        for refused in (fabrikam.id, None):
            with pytest.raises(Exception) as error:
                await create_identity(db_session, holder, IdentityCreate(name="Nope", organization_id=refused))
            assert getattr(error.value, "status_code", None) == 403

    async def test_a_privileged_identity_is_changed_only_by_a_platform_admin(self, db_session) -> None:
        from src.models import Role, RolePermission
        from src.services.user_role_assignments import insert_assignment

        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        holder = _lifecycle_holder(contoso.id)
        privileged = await create_identity(db_session, admin, IdentityCreate(name="Powerful", organization_id=contoso.id))
        role = Role(name=f"ids-role-{uuid4().hex[:6]}", created_by="t")
        db_session.add(role)
        await db_session.flush()
        db_session.add(RolePermission(role_id=role.id, permission="users.lifecycle.readwrite"))
        await db_session.flush()
        await insert_assignment(
            db_session,
            user_id=privileged.id,
            role_id=role.id,
            boundaries=[Boundary(BoundaryKind.ORGANIZATION, contoso.id)],
            assigned_by="t",
        )

        for attempt in (
            rename_identity(db_session, holder, privileged.id, IdentityUpdate(name="Renamed")),
            delete_identity(db_session, holder, privileged.id),
        ):
            with pytest.raises(Exception) as refused:
                await attempt
            assert getattr(refused.value, "status_code", None) == 403
        renamed = await rename_identity(db_session, admin, privileged.id, IdentityUpdate(name="Renamed"))
        assert renamed.name == "Renamed"
        assert [r.name for r in renamed.additional_roles] == [role.name]

    async def test_a_default_identity_is_never_deleted(self, db_session) -> None:
        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        default = await _by_kind(db_session, admin, contoso.id, "org_default")

        with pytest.raises(IdentityError) as refused:
            await delete_identity(db_session, admin, default.id)
        assert (refused.value.status_code, refused.value.detail) == (409, DEFAULT_DELETE_MESSAGE)

    async def test_a_default_identity_is_never_renamed(self, db_session) -> None:
        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        default = await _by_kind(db_session, admin, contoso.id, "org_default")

        with pytest.raises(IdentityError) as refused:
            await rename_identity(db_session, admin, default.id, IdentityUpdate(name="Contoso Robot"))
        assert (refused.value.status_code, refused.value.detail) == (409, DEFAULT_RENAME_MESSAGE)
        assert (await _by_kind(db_session, admin, contoso.id, "org_default")).name == "Default Identity"

    async def test_a_taken_name_is_refused_and_the_session_carries_on(self, db_session) -> None:
        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        await create_identity(db_session, admin, IdentityCreate(name="Nightly", organization_id=contoso.id))
        other = await create_identity(db_session, admin, IdentityCreate(name="Other", organization_id=contoso.id))

        with pytest.raises(IdentityError) as refused:
            await create_identity(db_session, admin, IdentityCreate(name="NIGHTLY", organization_id=contoso.id))
        assert (refused.value.status_code, refused.value.detail) == (
            409,
            f'An identity named "NIGHTLY" already exists in {contoso.name}',
        )
        with pytest.raises(IdentityError) as refused:
            await rename_identity(db_session, admin, other.id, IdentityUpdate(name="nightly"))
        assert refused.value.status_code == 409

        names = {i.name for i in await list_identities(db_session, admin, organization_id=contoso.id)}
        assert names == {"Default Identity", "Nightly", "Other"}

    async def test_delete_names_the_workflows_that_run_as_it_up_to_ten(self, db_session) -> None:
        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        custom = await create_identity(db_session, admin, IdentityCreate(name="Busy", organization_id=contoso.id))
        for index in range(12):
            await _workflow(db_session, contoso.id, name=f"wf_{index:02d}", run_identity_id=custom.id)

        with pytest.raises(IdentityError) as refused:
            await delete_identity(db_session, admin, custom.id)
        detail = refused.value.detail
        assert refused.value.status_code == 409
        assert detail.startswith(f"Can't delete Busy ({contoso.name}): these workflows run as it: wf_00, wf_01")
        assert "wf_09" in detail and "wf_10" not in detail and detail.endswith("and 2 more")

    async def test_a_workflow_with_a_blank_display_name_is_named_by_its_name(self, db_session) -> None:
        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        custom = await create_identity(db_session, admin, IdentityCreate(name="Blank", organization_id=contoso.id))
        await _workflow(db_session, contoso.id, name="sync_invoices", run_identity_id=custom.id, display_name="")

        with pytest.raises(IdentityError) as refused:
            await delete_identity(db_session, admin, custom.id)
        assert refused.value.detail.endswith("these workflows run as it: sync_invoices")

    async def test_a_workflow_pointed_at_it_during_the_delete_is_named_too(self, db_session, monkeypatch) -> None:
        """The race: the check finds no workflow, then one is pointed at the
        identity before the delete flushes; the foreign key refuses it."""
        from sqlalchemy import insert

        from src.models.orm.workflows import Workflow

        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        custom = await create_identity(db_session, admin, IdentityCreate(name="Racing", organization_id=contoso.id))
        execute = db_session.execute

        async def point_after_the_check(statement, *args, **kwargs):
            result = await execute(statement, *args, **kwargs)
            if "run_identity_id" in str(statement) and not hasattr(point_after_the_check, "done"):
                point_after_the_check.done = True
                await execute(
                    insert(Workflow).values(
                        name="late", function_name="late", path="workflows/late.py",
                        organization_id=contoso.id, run_identity_id=custom.id,
                    )
                )
            return result

        monkeypatch.setattr(db_session, "execute", point_after_the_check)
        with pytest.raises(IdentityError) as refused:
            await delete_identity(db_session, admin, custom.id)

        assert (refused.value.status_code, refused.value.detail) == (
            409,
            f"Can't delete Racing ({contoso.name}): these workflows run as it: late",
        )
        assert custom.id in {i.id for i in await list_identities(db_session, admin, organization_id=contoso.id)}


def _holding(permissions: set[str], *organization_ids: UUID) -> Caller:
    """Holds ``permissions`` at each organization through one role, and nothing else."""
    ctx = AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=PROVIDER_ORG_ID,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=USER_BASE_PERMISSIONS,
        role_grants=(
            RoleGrant(
                uuid4(),
                frozenset(permissions),
                tuple(Boundary(BoundaryKind.ORGANIZATION, org_id) for org_id in organization_ids),
            ),
        ),
    )
    return Caller(UserPrincipal(user_id=ctx.user_id, email="holder@example.com", organization_id=PROVIDER_ORG_ID), ctx)


async def _give(db_session, identity: IdentityPublic, permissions: set[str], organization_id: UUID) -> None:
    from src.models import Role, RolePermission
    from src.services.user_role_assignments import insert_assignment

    role = Role(name=f"delegation-{uuid4().hex[:6]}", created_by="t")
    db_session.add(role)
    await db_session.flush()
    db_session.add_all(RolePermission(role_id=role.id, permission=permission) for permission in permissions)
    await db_session.flush()
    await insert_assignment(
        db_session,
        user_id=identity.id,
        role_id=role.id,
        boundaries=[Boundary(BoundaryKind.ORGANIZATION, organization_id)],
        assigned_by="t",
    )


class TestRunIdentities:
    async def test_an_organization_workflow_may_run_as_that_organizations_identities_only(self, db_session) -> None:
        admin = admin_caller()
        contoso, fabrikam = await _organization(db_session, "Contoso"), await _organization(db_session, "Fabrikam")
        custom = await create_identity(db_session, admin, IdentityCreate(name="Nightly", organization_id=contoso.id))
        await create_identity(db_session, admin, IdentityCreate(name="Other", organization_id=fabrikam.id))

        listed = await list_run_identities(db_session, contoso.id)

        assert [(i.identity_kind, i.organization_id) for i in listed] == [
            ("org_default", contoso.id),
            ("custom", contoso.id),
        ]
        assert listed[1].id == custom.id

    async def test_a_global_workflow_may_run_as_global_or_provider_identities(self, db_session) -> None:
        admin = admin_caller()
        contoso = await _organization(db_session, "Contoso")
        shared = await create_identity(db_session, admin, IdentityCreate(name="Shared", organization_id=None))

        listed = await list_run_identities(db_session, None)

        assert {i.organization_id for i in listed} == {None, PROVIDER_ORG_ID}
        assert listed[0].identity_kind == IdentityKind.GLOBAL_DEFAULT
        assert shared.id in {i.id for i in listed}
        assert contoso.id not in {i.organization_id for i in listed}


class TestDelegation:
    async def test_an_identity_holding_only_the_user_baseline_needs_no_powers(self, db_session) -> None:
        contoso = await _organization(db_session, "Contoso")
        default = await _by_kind(db_session, admin_caller(), contoso.id, "org_default")

        await require_delegation(db_session, _holding(set()), default.id)

    async def test_the_caller_must_hold_every_power_the_identity_holds_where_it_holds_it(self, db_session) -> None:
        contoso, fabrikam = await _organization(db_session, "Contoso"), await _organization(db_session, "Fabrikam")
        custom = await create_identity(db_session, admin_caller(), IdentityCreate(name="Writer", organization_id=contoso.id))
        await _give(db_session, custom, {"workflows.readwrite", "tables.read"}, contoso.id)

        await require_delegation(db_session, _holding({"workflows.readwrite", "tables.read"}, contoso.id), custom.id)
        for caller in (
            _holding(set()),
            _holding({"workflows.readwrite"}, contoso.id),
            _holding({"workflows.readwrite", "tables.read"}, fabrikam.id),
        ):
            with pytest.raises(HTTPException) as refused:
                await require_delegation(db_session, caller, custom.id)
            assert (refused.value.status_code, refused.value.detail) == (
                403,
                f"Writer ({contoso.name}) holds powers you don't, so you can't make a workflow run as it",
            )

    async def test_a_platform_admin_may_delegate_any_identity(self, db_session) -> None:
        contoso = await _organization(db_session, "Contoso")
        custom = await create_identity(db_session, admin_caller(), IdentityCreate(name="Writer", organization_id=contoso.id))
        await _give(db_session, custom, {"workflows.readwrite"}, contoso.id)

        await require_delegation(db_session, admin_caller(), custom.id)
