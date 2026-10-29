"""Solution targets, backup material, and job ownership use original authority."""

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.contracts.solutions import SolutionExportJobCreate, SolutionExportOptions, SolutionUpdate
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.organizations import Organization
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.routers import solutions
from src.services.authorization.enforce import load_caller
from src.services.solutions.authorization import authorize_backup_archive, authorize_solution
from src.services.solutions.capture import SolutionCaptureService
from src.services.solutions.export_jobs import list_export_jobs


async def _setup(db, *permissions):
    home, foreign = (
        Organization(name=name, created_by="test") for name in ("Contoso", "Fabrikam")
    )
    db.add_all([home, foreign])
    await db.flush()
    person = User(
        email=f"solution-{uuid4().hex}@contoso.example", organization_id=home.id,
        base_role_id=USER_ROLE_ID,
    )
    role = Role(name=f"Contoso Solutions {uuid4().hex}", created_by="test")
    db.add_all([person, role])
    await db.flush()
    db.add_all([
        UserRole(user_id=person.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=person.id, role_id=role.id, kind="organization", organization_id=home.id),
        *(RolePermission(role_id=role.id, permission=permission) for permission in permissions),
    ])
    rows = [
        Solution(slug=f"contoso-{uuid4().hex}", name="Contoso", organization_id=home.id),
        Solution(slug=f"fabrikam-{uuid4().hex}", name="Fabrikam", organization_id=foreign.id),
        Solution(slug=f"shared-{uuid4().hex}", name="Shared", organization_id=None),
    ]
    db.add_all(rows)
    await db.flush()
    # A copied actor flag supplies no authorization.
    principal = UserPrincipal(user_id=person.id, email=person.email, organization_id=home.id, is_superuser=True)
    return principal, rows


async def test_solution_list_filters_permission_and_reach_before_aggregates(db_session, monkeypatch):
    principal, rows = await _setup(db_session, "solutions.read")
    sdk = AsyncMock(return_value={})
    monkeypatch.setattr(solutions, "_solution_sdk_statuses_for_rows", sdk)
    result = await solutions.list_solutions(SimpleNamespace(db=db_session), principal)
    assert [row.id for row in result.solutions] == [rows[0].id]
    assert [row.id for row in sdk.await_args.args[1]] == [rows[0].id]


async def test_solution_read_grant_does_not_authorize_another_org_or_global(db_session):
    principal, rows = await _setup(db_session, "solutions.read")
    await authorize_solution(db_session, principal, rows[0].id, "solutions.read")
    for row, status in ((rows[1], 404), (rows[2], 403)):
        with pytest.raises(HTTPException) as error:
            await authorize_solution(db_session, principal, row.id, "solutions.read")
        assert error.value.status_code == status


async def test_solution_move_checks_destination_before_mutating(db_session):
    principal, rows = await _setup(db_session, "solutions.readwrite")
    with pytest.raises(HTTPException) as error:
        await solutions.update_solution(
            rows[0].id, SolutionUpdate(organization_id=rows[1].organization_id),
            SimpleNamespace(db=db_session), principal,
        )
    assert error.value.status_code == 403
    assert rows[0].organization_id == principal.organization_id


async def test_secret_export_refuses_before_job_or_crypto_work(db_session, monkeypatch):
    principal, rows = await _setup(db_session, "solutions.readwrite")
    create = AsyncMock()
    monkeypatch.setattr(solutions, "create_export_job", create)
    with pytest.raises(HTTPException) as error:
        await solutions.create_solution_export_job(
            rows[0].id,
            SolutionExportJobCreate(options=SolutionExportOptions(include_secrets=True, password="test password")),
            SimpleNamespace(db=db_session), principal,
        )
    assert error.value.status_code == 403
    create.assert_not_awaited()


async def test_backup_archive_requires_secrets_before_password_validation(db_session, tmp_path):
    principal, rows = await _setup(db_session, "solutions.deploy")
    caller = await load_caller(db_session, principal)
    archive = tmp_path / "contoso.zip"
    with ZipFile(archive, "w") as content:
        content.writestr(".bifrost/secrets.enc", "opaque test payload")
    with pytest.raises(HTTPException) as error:
        authorize_backup_archive(caller, archive, rows[0].organization_id)
    assert error.value.status_code == 403


async def test_nonsecret_backup_does_not_load_or_decrypt_secret_values(db_session, monkeypatch):
    _principal, rows = await _setup(db_session, "solutions.read")
    for key, schema_type, value_type in (
        ("endpoint", "string", ConfigType.STRING),
        ("credential", "secret", ConfigType.SECRET),
        ("changed_type", "string", ConfigType.SECRET),
    ):
        db_session.add(SolutionConfigSchema(solution_id=rows[0].id, key=key, type=schema_type))
        db_session.add(Config(
            key=key, organization_id=rows[0].organization_id, config_type=value_type,
            value={"value": "opaque test value"}, updated_by="test",
        ))
    await db_session.flush()
    decrypt = Mock(side_effect=AssertionError("secret decryption was not selected"))
    monkeypatch.setattr("src.core.security.decrypt_secret", decrypt)
    values = await SolutionCaptureService(db_session)._config_values(rows[0], include_secrets=False)
    assert values == {"endpoint": "opaque test value"}
    decrypt.assert_not_called()


async def _export_job(db, solution, requested_by, *, secret=False, created_at=None):
    row = SolutionExportJob(
        solution_id=solution.id, organization_id=solution.organization_id,
        contains_secrets=secret, status="completed", progress_percent=100,
        artifact_storage_key="solution-exports/contoso/backup.zip",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(row)
    await db.flush()
    db.add(PlatformJob(
        job_type="solution.export", payload={}, organization_id=solution.organization_id,
        resource_type="solution_export", resource_id=str(row.id),
        requested_by_user_id=str(requested_by), requested_by_email="person@contoso.example",
        requested_by_name="Contoso User", title="Contoso Export",
    ))
    await db.flush()
    return row


@pytest.mark.parametrize("secret", [False, True])
async def test_own_backup_download_requires_secrets_only_for_secret_archives(db_session, secret):
    principal, rows = await _setup(db_session, "solutions.read")
    job = await _export_job(db_session, rows[0], principal.user_id, secret=secret)
    if secret:
        with pytest.raises(HTTPException) as error:
            await solutions._downloadable_export_job(db_session, job.id, principal)
        assert error.value.status_code == 403
    else:
        assert await solutions._downloadable_export_job(db_session, job.id, principal) is job


async def test_solution_read_does_not_expose_another_requesters_export(db_session):
    principal, rows = await _setup(db_session, "solutions.read")
    job = await _export_job(db_session, rows[0], uuid4())
    with pytest.raises(HTTPException) as error:
        await solutions.get_solution_export_job(job.id, SimpleNamespace(db=db_session), principal)
    assert error.value.status_code == 404


async def test_export_job_list_filters_private_jobs_before_limiting(db_session):
    principal, rows = await _setup(db_session, "solutions.read")
    own = await _export_job(
        db_session, rows[0], principal.user_id,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    await _export_job(db_session, rows[0], uuid4())
    caller = await load_caller(db_session, principal)
    result = await list_export_jobs(db_session, rows[0].id, caller=caller, limit=1)
    assert [job.id for job in result] == [own.id]
