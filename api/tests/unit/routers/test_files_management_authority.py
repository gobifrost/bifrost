"""Workspace and file-policy management use named grants at their actual scope."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from shared.file_access import FileServiceError, install_org_id
from src.core.principal import UserPrincipal
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.routers import files


async def _person(db, *permissions, global_grant=False, actor_flag=False):
    home, foreign = [Organization(name=name, created_by="test") for name in ("Contoso", "Fabrikam")]
    db.add_all([home, foreign])
    await db.flush()
    person = User(email=f"files-{uuid4().hex}@contoso.example", organization_id=home.id, base_role_id=USER_ROLE_ID)
    role = Role(name=f"Contoso Files {uuid4().hex}", created_by="test")
    db.add_all([person, role])
    await db.flush()
    db.add_all([
        UserRole(user_id=person.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=person.id, role_id=role.id,
                         kind="platform" if global_grant else "organization",
                         organization_id=None if global_grant else home.id),
        *(RolePermission(role_id=role.id, permission=permission) for permission in permissions),
    ])
    await db.flush()
    user = UserPrincipal(user_id=person.id, email=person.email, organization_id=home.id, is_superuser=actor_flag)
    return user, home, foreign


async def test_workspace_read_grant_admits_watcher_snapshot_without_admin_flag(db_session, monkeypatch):
    user, _home, _foreign = await _person(db_session, "repository.read", global_grant=True)

    async def no_keys(*_args):
        for key in ():
            yield key

    redis = SimpleNamespace(scan_iter=no_keys)
    connect = AsyncMock(return_value=redis)
    monkeypatch.setattr("src.core.cache.redis_client.get_shared_redis", connect)
    assert await files.list_active_watchers(user, db_session) == {"watchers": []}
    connect.assert_awaited_once()


@pytest.mark.parametrize("global_grant", [False, True])
async def test_workspace_write_refuses_read_only_grant_before_storage(db_session, monkeypatch, global_grant):
    user, _home, _foreign = await _person(db_session, "repository.read", global_grant=global_grant, actor_flag=True)
    storage = Mock(side_effect=AssertionError("storage initialized before authorization"))
    monkeypatch.setattr("src.services.file_storage.FileStorageService", storage)
    with pytest.raises(HTTPException) as error:
        await files.create_folder_editor(SimpleNamespace(), user, "reports", db_session)
    assert error.value.status_code == 403
    storage.assert_not_called()


async def test_policy_listing_uses_requested_org_when_location_is_omitted(db_session):
    user, home, foreign = await _person(db_session, "filepolicies.read")
    for org in (home, foreign):
        db_session.add(FilePolicy(organization_id=org.id, location="reports", path="", policies={"policies": []}))
    await db_session.flush()
    ctx = SimpleNamespace(user=user, db=db_session, org_id=foreign.id)
    result = await files.list_file_policies(ctx, user, location=None, scope=str(home.id), organization_id=None, solution=None, db=db_session)
    assert len(result.policies) == 1
    assert result.policies[0].organization_id == str(home.id)


async def test_solution_policy_read_checks_persisted_install_org(db_session):
    user, home, foreign = await _person(db_session, "filepolicies.read", actor_flag=True)
    solution = Solution(slug=f"fabrikam-{uuid4().hex}", name="Fabrikam", organization_id=foreign.id)
    db_session.add(solution)
    await db_session.flush()
    ctx = SimpleNamespace(user=user, db=db_session, org_id=home.id)
    with pytest.raises(HTTPException) as error:
        await files.list_file_policies(ctx, user, location=None, scope=None, organization_id=None, solution=str(solution.id), db=db_session)
    assert error.value.status_code == 404


@pytest.mark.parametrize("repository_grant", [False, True])
async def test_workspace_access_test_uses_repository_permission(db_session, repository_grant):
    permissions = ["filepolicies.read"] + (["repository.read"] if repository_grant else [])
    user, home, _foreign = await _person(db_session, *permissions, global_grant=True, actor_flag=not repository_grant)
    ctx = SimpleNamespace(user=user, db=db_session, org_id=home.id, solution_id=None)
    result = await files.test_file_policy_access(files.FilePolicyAccessTestRequest(path="workflows/test.py", action="read"), ctx, user, db_session)
    assert result.allowed is repository_grant
    assert result.matched_rule == ("repository.read" if repository_grant else None)


async def test_missing_install_does_not_inherit_context_organization(db_session):
    with pytest.raises(FileServiceError) as error:
        await install_org_id(SimpleNamespace(db=db_session, org_id=uuid4()), uuid4())
    assert error.value.status_code == 404


async def test_solution_structure_separates_install_storage_from_org_authorization(db_session, monkeypatch):
    user, home, _foreign = await _person(db_session, "filepolicies.read")
    solution = Solution(slug=f"contoso-{uuid4().hex}", name="Contoso", organization_id=home.id)
    db_session.add(solution)
    await db_session.flush()
    service = SimpleNamespace(list_prefix=AsyncMock(return_value=[]))
    monkeypatch.setattr("src.services.file_structure_service.FileStructureService", Mock(return_value=service))
    ctx = SimpleNamespace(user=user, db=db_session, org_id=None, solution_id=str(solution.id))

    result = await files.list_file_structure(
        files.FileStructureRequest(location="reports", prefix="monthly"), ctx, user, db_session,
    )

    assert result.entries == []
    service.list_prefix.assert_awaited_once_with(
        org_id=home.id, solution_id=solution.id, location="reports", prefix="monthly",
    )


async def test_solution_structure_refuses_foreign_install_before_storage(db_session, monkeypatch):
    user, home, foreign = await _person(db_session, "filepolicies.read", actor_flag=True)
    solution = Solution(slug=f"fabrikam-{uuid4().hex}", name="Fabrikam", organization_id=foreign.id)
    db_session.add(solution)
    await db_session.flush()
    storage = Mock(side_effect=AssertionError("storage before install authorization"))
    monkeypatch.setattr("src.services.file_structure_service.FileStructureService", storage)
    ctx = SimpleNamespace(user=user, db=db_session, org_id=home.id, solution_id=str(solution.id))
    with pytest.raises(HTTPException) as error:
        await files.list_file_structure(files.FileStructureRequest(location="reports"), ctx, user, db_session)
    assert error.value.status_code == 404
    storage.assert_not_called()


async def test_invalid_solution_structure_context_does_not_fall_back_to_home(db_session, monkeypatch):
    user, home, _foreign = await _person(db_session, "filepolicies.read")
    storage = Mock(side_effect=AssertionError("storage before context validation"))
    monkeypatch.setattr("src.services.file_structure_service.FileStructureService", storage)
    ctx = SimpleNamespace(user=user, db=db_session, org_id=home.id, solution_id="invalid-install")
    with pytest.raises(HTTPException) as error:
        await files.list_file_structure(files.FileStructureRequest(location="reports"), ctx, user, db_session)
    assert error.value.status_code == 400
    storage.assert_not_called()
