"""Real Builder Global operation review/apply/rollback coverage."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
import tempfile
from uuid import UUID

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.builder_global_release import (
    BuilderGlobalReleaseApplyPayload,
    run_builder_global_release_apply,
)
from src.models.orm.applications import Application
from src.models.orm.forms import Form
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionGlobalOperationChange,
    SolutionGlobalWorkspaceApply,
    SolutionSourceRevision,
)
from src.models.orm.tables import Document, Table
from src.models.orm.workflows import Workflow
from src.services.builder.fs_tools import WorkspaceLimits, safe_extract_zip
from src.services.builder.global_operation_changes import (
    _fingerprint,
    operation_change_review_fingerprint,
)
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.scaffold import zip_workspace
from src.services.repo_storage import RepoStorage
from src.services.repo_sync_writer import RepoSyncWriter
from tests.e2e.fixtures.setup import _login_user

pytestmark = pytest.mark.e2e

BUILDER_URL = "/api/builder/solutions"


@dataclass
class _JobContext:
    job_id: UUID
    lease_token: UUID
    organization_id: UUID | None
    requested_by_user_id: str
    requested_by_email: str
    requested_by_name: str

    async def report(self, *_args, **_kwargs) -> None:
        return None

    async def log(self, *_args, **_kwargs) -> None:
        return None


def _platform(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "X-Bifrost-Boundary": "platform"}


def _org(headers: dict[str, str], org_id: UUID | str) -> dict[str, str]:
    return {**headers, "X-Bifrost-Boundary": f"organization:{org_id}"}


def _poll_job(e2e_client, headers: dict[str, str], job_id: str) -> dict:
    for _ in range(120):
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(0.25)
    raise AssertionError(f"platform job {job_id} did not finish")


@pytest.fixture
def platform_builder_role(e2e_client, platform_admin):
    headers = _platform(platform_admin.headers)
    response = e2e_client.post(
        "/api/roles",
        headers=headers,
        json={
            "name": f"E2E Platform Builder {uuid.uuid4().hex[:8]}",
            "description": "Global Builder operation e2e",
            "capabilities": [
                "builder.read",
                "builder.execute",
                "repository.readwrite",
                "agents.read",
                "agents.readwrite",
                "forms.read",
                "forms.readwrite",
                "tables.read",
                "tables.readwrite",
                "workflows.read",
                "workflows.readwrite",
                "apps.read",
                "apps.readwrite",
            ],
        },
    )
    assert response.status_code == 201, response.text
    role = response.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=headers)


def _assign_role(e2e_client, platform_admin, role_id: str, user_id: UUID, boundary: dict):
    response = e2e_client.post(
        f"/api/roles/{role_id}/users",
        headers=_platform(platform_admin.headers),
        json={"user_ids": [str(user_id)], "boundaries": [boundary]},
    )
    assert response.status_code == 204, response.text


def _remove_role(e2e_client, platform_admin, role_id: str, user_id: UUID):
    e2e_client.delete(
        f"/api/roles/{role_id}/users/{user_id}",
        headers=_platform(platform_admin.headers),
    )


@pytest.fixture
def two_platform_builders(
    e2e_client,
    platform_admin,
    platform_builder_role,
    alice_user,
    bob_user,
):
    boundary = {"boundary_kind": "platform", "organization_id": None}
    _assign_role(e2e_client, platform_admin, platform_builder_role["id"], alice_user.user_id, boundary)
    _assign_role(e2e_client, platform_admin, platform_builder_role["id"], bob_user.user_id, boundary)
    _login_user(e2e_client, alice_user)
    _login_user(e2e_client, bob_user)
    yield alice_user, bob_user
    _remove_role(e2e_client, platform_admin, platform_builder_role["id"], alice_user.user_id)
    _remove_role(e2e_client, platform_admin, platform_builder_role["id"], bob_user.user_id)
    _login_user(e2e_client, alice_user)
    _login_user(e2e_client, bob_user)


async def _stage_create(db, solution_id: UUID, *, name: str) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="agents.create",
        resource_type="agent",
        state="staged",
        payload={
            "name": name,
            "system_prompt": "You are a test global agent.",
            "access_level": "authenticated",
            "organization_id": None,
        },
        before_state=None,
        before_fingerprint=None,
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _stage_update(
    db,
    solution_id: UUID,
    *,
    before: dict,
    name: str,
) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="agents.update",
        resource_type="agent",
        resource_id=before["id"],
        state="staged",
        payload={"name": name, "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _stage_form_create(db, solution_id: UUID, *, name: str) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="forms.create",
        resource_type="form",
        state="staged",
        payload={
            "name": name,
            "description": "Created through reviewed Global Builder operations.",
            "form_schema": {"fields": [{"name": "email", "type": "text", "label": "Email"}]},
            "access_level": "authenticated",
            "organization_id": None,
        },
        before_state=None,
        before_fingerprint=None,
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _stage_form_update(
    db,
    solution_id: UUID,
    *,
    before: dict,
    name: str,
) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="forms.update",
        resource_type="form",
        resource_id=before["id"],
        state="staged",
        payload={"name": name, "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _stage_table_create(db, solution_id: UUID, *, name: str) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="tables.create",
        resource_type="table",
        state="staged",
        payload={
            "name": name,
            "description": "Created through reviewed Global Builder operations.",
            "schema": {"columns": [{"name": "email", "type": "string"}]},
            "organization_id": None,
        },
        before_state=None,
        before_fingerprint=None,
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _stage_table_update(
    db,
    solution_id: UUID,
    *,
    before: dict,
    name: str,
) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="tables.update",
        resource_type="table",
        resource_id=before["id"],
        state="staged",
        payload={"name": name, "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _stage_workflow_update(
    db,
    solution_id: UUID,
    *,
    before: dict,
    display_name: str,
) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="workflows.update",
        resource_type="workflow",
        resource_id=before["id"],
        state="staged",
        payload={"display_name": display_name, "organization_id": None},
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _stage_app_update(
    db,
    solution_id: UUID,
    *,
    before: dict,
    name: str,
    slug: str,
) -> SolutionGlobalOperationChange:
    row = SolutionGlobalOperationChange(
        id=uuid.uuid4(),
        solution_id=solution_id,
        operation_id="apps.update",
        resource_type="application",
        resource_id=before["id"],
        state="staged",
        payload={
            "name": name,
            "slug": slug,
            "description": "Updated app description",
            "icon": "layout-dashboard",
            "access_level": "everyone",
            "organization_id": None,
        },
        before_state=before,
        before_fingerprint=_fingerprint(before),
        validation_errors=[],
    )
    db.add(row)
    await db.commit()
    return row


async def _create_source_proposal_revision(
    db,
    *,
    solution_id: UUID,
    project,
    requested_by: UUID,
    path: str,
    content: bytes,
) -> SolutionSourceRevision:
    assert project.current_revision_id == project.deployed_revision_id
    baseline_id = project.deployed_revision_id
    assert baseline_id is not None
    revision_id = uuid.uuid4()
    limits = WorkspaceLimits()
    with tempfile.TemporaryDirectory(prefix="bifrost-combined-release-e2e-") as tmp:
        root = Path(tmp)
        baseline_archive = root / "baseline.zip"
        copied = await SolutionRevisionStorage(solution_id).copy_to_path(
            baseline_id,
            baseline_archive,
        )
        assert copied, "baseline revision archive missing"
        workspace = root / "workspace"
        workspace.mkdir()
        safe_extract_zip(baseline_archive, workspace, limits)
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        proposal_archive = root / "proposal.zip"
        digest = zip_workspace(workspace, proposal_archive)
        await SolutionRevisionStorage(solution_id).write_from_path(
            revision_id,
            proposal_archive,
        )
        size = proposal_archive.stat().st_size
    revision = SolutionSourceRevision(
        id=revision_id,
        solution_id=solution_id,
        parent_revision_id=baseline_id,
        created_by=requested_by,
        source_sha256=digest,
        size_bytes=size,
        summary=f"combined release e2e proposal for {path}",
    )
    db.add(revision)
    await db.flush()
    project.current_revision_id = revision.id
    await db.commit()
    return revision


@pytest.mark.asyncio
async def test_two_platform_builders_share_global_workspace_and_apply_rollback_agents(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])

    opened_by_bob = e2e_client.get(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(bob.headers),
    )
    assert opened_by_bob.status_code == 200, opened_by_bob.text
    assert opened_by_bob.json()["solution_id"] == str(solution_id)

    session = e2e_client.post(
        f"{BUILDER_URL}/{solution_id}/sessions",
        headers=_platform(bob.headers),
        json={"title": "Support global build"},
    )
    assert session.status_code == 201, session.text

    unique_name = f"E2E Global Agent {uuid.uuid4().hex[:8]}"
    async with async_session_factory() as db:
        await _stage_create(db, solution_id, name=unique_name)

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(alice.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(alice.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed

    agent = e2e_client.get("/api/agents", headers=_platform(alice.headers))
    assert agent.status_code == 200, agent.text
    created_agent = next(item for item in agent.json() if item["name"] == unique_name)
    assert created_agent["organization_id"] is None

    before = e2e_client.get(
        f"/api/agents/{created_agent['id']}",
        headers=_platform(alice.headers),
    )
    assert before.status_code == 200, before.text
    updated_name = f"{unique_name} Updated"
    async with async_session_factory() as db:
        await _stage_update(db, solution_id, before=before.json(), name=updated_name)

    accepted_update = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(bob.headers),
    )
    assert accepted_update.status_code == 202, accepted_update.text
    completed_update = _poll_job(
        e2e_client,
        _platform(bob.headers),
        accepted_update.json()["job_id"],
    )
    assert completed_update["status"] == "succeeded", completed_update
    updated = e2e_client.get(
        f"/api/agents/{created_agent['id']}",
        headers=_platform(alice.headers),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == updated_name

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back

    restored = e2e_client.get(
        f"/api/agents/{created_agent['id']}",
        headers=_platform(alice.headers),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["name"] == unique_name

    e2e_client.delete(f"/api/agents/{created_agent['id']}", headers=_platform(alice.headers))


@pytest.mark.asyncio
async def test_platform_builder_can_apply_and_rollback_global_form_create(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, _bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    form_name = f"E2E Global Form {uuid.uuid4().hex[:8]}"
    async with async_session_factory() as db:
        await _stage_form_create(db, solution_id, name=form_name)

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(alice.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(alice.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed

    forms = e2e_client.get("/api/forms", headers=_platform(alice.headers))
    assert forms.status_code == 200, forms.text
    created_form = next(item for item in forms.json() if item["name"] == form_name)
    assert created_form["organization_id"] is None

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back

    missing = e2e_client.get(
        f"/api/forms/{created_form['id']}",
        headers=_platform(alice.headers),
    )
    assert missing.status_code == 404, missing.text
    async with async_session_factory() as db:
        assert await db.get(Form, UUID(created_form["id"])) is None


@pytest.mark.asyncio
async def test_platform_builder_can_apply_and_rollback_global_form_update(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    form_name = f"E2E Existing Form {uuid.uuid4().hex[:8]}"
    create_form = e2e_client.post(
        "/api/forms",
        headers=_platform(alice.headers),
        json={
            "name": form_name,
            "description": None,
            "form_schema": {"fields": [{"name": "email", "type": "text", "label": "Email"}]},
            "access_level": "authenticated",
            "organization_id": None,
        },
    )
    assert create_form.status_code == 201, create_form.text
    form_id = create_form.json()["id"]
    before = e2e_client.get(f"/api/forms/{form_id}", headers=_platform(alice.headers))
    assert before.status_code == 200, before.text
    updated_name = f"{form_name} Updated"
    async with async_session_factory() as db:
        await _stage_form_update(db, solution_id, before=before.json(), name=updated_name)

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(bob.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(bob.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    updated = e2e_client.get(f"/api/forms/{form_id}", headers=_platform(alice.headers))
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == updated_name

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back
    restored = e2e_client.get(f"/api/forms/{form_id}", headers=_platform(alice.headers))
    assert restored.status_code == 200, restored.text
    assert restored.json()["name"] == form_name

    e2e_client.delete(f"/api/forms/{form_id}?purge=true", headers=_platform(alice.headers))


@pytest.mark.asyncio
async def test_platform_builder_can_apply_and_rollback_global_table_create(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, _bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    table_name = f"e2e_global_table_{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as db:
        await _stage_table_create(db, solution_id, name=table_name)

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(alice.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(alice.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed

    tables = e2e_client.get("/api/tables", headers=_platform(alice.headers))
    assert tables.status_code == 200, tables.text
    created_table = next(item for item in tables.json()["tables"] if item["name"] == table_name)
    assert created_table["organization_id"] is None

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back

    missing = e2e_client.get(
        f"/api/tables/{created_table['id']}",
        headers=_platform(alice.headers),
    )
    assert missing.status_code == 404, missing.text
    async with async_session_factory() as db:
        assert await db.get(Table, UUID(created_table["id"])) is None


@pytest.mark.asyncio
async def test_global_table_create_rollback_fails_when_documents_exist(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, _bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    table_name = f"e2e_global_table_docs_{uuid.uuid4().hex[:8]}"
    async with async_session_factory() as db:
        await _stage_table_create(db, solution_id, name=table_name)

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(alice.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(alice.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    tables = e2e_client.get("/api/tables", headers=_platform(alice.headers))
    assert tables.status_code == 200, tables.text
    created_table = next(item for item in tables.json()["tables"] if item["name"] == table_name)

    async with async_session_factory() as db:
        db.add(
            Document(
                table_id=UUID(created_table["id"]),
                id="row-1",
                data={"email": "customer@example.test"},
                created_by=alice.email,
                updated_by=alice.email,
            )
        )
        await db.commit()

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "failed", rolled_back
    assert rolled_back["error"]["code"] == "global_operation_rollback_failed"
    assert "documents=1" in rolled_back["error"]["message"]
    still_there = e2e_client.get(
        f"/api/tables/{created_table['id']}",
        headers=_platform(alice.headers),
    )
    assert still_there.status_code == 200, still_there.text

    e2e_client.delete(f"/api/tables/{created_table['id']}", headers=_platform(alice.headers))


@pytest.mark.asyncio
async def test_platform_builder_can_apply_and_rollback_global_table_update(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    table_name = f"e2e_existing_table_{uuid.uuid4().hex[:8]}"
    create_table = e2e_client.post(
        "/api/tables",
        headers=_platform(alice.headers),
        json={
            "name": table_name,
            "description": None,
            "schema": {"columns": [{"name": "email", "type": "string"}]},
            "organization_id": None,
        },
    )
    assert create_table.status_code == 201, create_table.text
    table_id = create_table.json()["id"]
    before = e2e_client.get(f"/api/tables/{table_id}", headers=_platform(alice.headers))
    assert before.status_code == 200, before.text
    updated_name = f"{table_name}_updated"
    async with async_session_factory() as db:
        await _stage_table_update(db, solution_id, before=before.json(), name=updated_name)

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(bob.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(bob.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    updated = e2e_client.get(f"/api/tables/{table_id}", headers=_platform(alice.headers))
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == updated_name

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back
    restored = e2e_client.get(f"/api/tables/{table_id}", headers=_platform(alice.headers))
    assert restored.status_code == 200, restored.text
    assert restored.json()["name"] == table_name

    e2e_client.delete(f"/api/tables/{table_id}", headers=_platform(alice.headers))


@pytest.mark.asyncio
async def test_platform_builder_can_apply_and_rollback_global_workflow_update(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    workflow_id = uuid.uuid4()
    function_name = f"e2e_global_workflow_{uuid.uuid4().hex[:8]}"
    original_display_name = "Original Global Workflow"
    async with async_session_factory() as db:
        db.add(
            Workflow(
                id=workflow_id,
                name=function_name,
                function_name=function_name,
                display_name=original_display_name,
                description="Original description",
                category="General",
                type="workflow",
                organization_id=None,
                path=f"workflows/{function_name}.py",
                access_level="authenticated",
                tags=[],
                is_active=True,
                endpoint_enabled=False,
                allowed_methods=["POST"],
                public_endpoint=False,
                disable_global_key=False,
                execution_mode="sync",
                timeout_seconds=300,
                tool_description="Original tool description",
                cache_ttl_seconds=300,
                time_saved=0,
                value=0,
            )
        )
        await db.commit()

    before = e2e_client.get(
        f"/api/workflows/{workflow_id}",
        headers=_platform(alice.headers),
    )
    assert before.status_code == 200, before.text
    updated_display_name = "Updated Global Workflow"
    async with async_session_factory() as db:
        await _stage_workflow_update(
            db,
            solution_id,
            before=before.json(),
            display_name=updated_display_name,
        )

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(bob.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(bob.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    updated = e2e_client.get(
        f"/api/workflows/{workflow_id}",
        headers=_platform(alice.headers),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["display_name"] == updated_display_name

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back
    restored = e2e_client.get(
        f"/api/workflows/{workflow_id}",
        headers=_platform(alice.headers),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["display_name"] == original_display_name

    async with async_session_factory() as db:
        workflow = await db.get(Workflow, workflow_id)
        if workflow is not None:
            await db.delete(workflow)
            await db.commit()


@pytest.mark.asyncio
async def test_global_combined_source_and_app_release_tracks_final_manifest_revision(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, bob = two_platform_builders
    app_id = uuid.uuid4()
    original_slug = f"e2e-combined-app-{uuid.uuid4().hex[:8]}"
    original_name = "Combined Original App"
    async with async_session_factory() as db:
        db.add(
            Application(
                id=app_id,
                name=original_name,
                slug=original_slug,
                description="Combined original app description",
                icon="app-window",
                repo_path=f"apps/{original_slug}",
                organization_id=None,
                app_model="inline_v1",
                access_level="authenticated",
                created_by="e2e@example.test",
            )
        )
        await db.flush()
        await RepoSyncWriter(db).regenerate_manifest()
        await db.commit()

    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    refreshed = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/refresh",
        headers=_platform(alice.headers),
    )
    assert refreshed.status_code == 200, refreshed.text
    source_path = f"workflows/e2e_combined_source_{uuid.uuid4().hex[:8]}.py"
    proposed_source = (
        "def combined_release_probe():\n"
        "    return {'status': 'combined-release'}\n"
    ).encode("utf-8")
    before = e2e_client.get(
        f"/api/applications/{original_slug}",
        headers=_platform(alice.headers),
    )
    assert before.status_code == 200, before.text
    updated_slug = f"{original_slug}-updated"
    updated_name = "Combined Updated App"
    async with async_session_factory() as db:
        project = await db.scalar(
            select(SolutionBuilderProject).where(
                SolutionBuilderProject.solution_id == solution_id
            )
        )
        assert project is not None
        baseline_revision_id = project.deployed_revision_id
        baseline_revision = await db.get(SolutionSourceRevision, baseline_revision_id)
        assert baseline_revision is not None
        proposal = await _create_source_proposal_revision(
            db,
            solution_id=solution_id,
            project=project,
            requested_by=alice.user_id,
            path=source_path,
            content=proposed_source,
        )
        await _stage_app_update(
            db,
            solution_id,
            before=before.json(),
            name=updated_name,
            slug=updated_slug,
        )

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(bob.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(bob.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    repo = RepoStorage()
    assert await repo.read(source_path) == proposed_source
    updated_manifest = (await repo.read(".bifrost/apps.yaml")).decode("utf-8")
    assert updated_name in updated_manifest
    assert updated_slug in updated_manifest

    async with async_session_factory() as db:
        project = await db.scalar(
            select(SolutionBuilderProject).where(
                SolutionBuilderProject.solution_id == solution_id
            )
        )
        assert project is not None
        apply_row = await db.scalar(
            select(SolutionGlobalWorkspaceApply).where(
                SolutionGlobalWorkspaceApply.solution_id == solution_id,
                SolutionGlobalWorkspaceApply.apply_job_id == UUID(accepted.json()["job_id"]),
            )
        )
        assert apply_row is not None
        assert apply_row.from_revision_id == baseline_revision_id
        assert apply_row.to_revision_id == proposal.id
        assert apply_row.released_revision_id is not None
        assert project.current_revision_id == apply_row.released_revision_id
        assert project.deployed_revision_id == apply_row.released_revision_id
        released = await db.get(SolutionSourceRevision, apply_row.released_revision_id)
        assert released is not None
        assert released.source_sha256 not in {
            baseline_revision.source_sha256,
            proposal.source_sha256,
        }

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back
    with pytest.raises(ClientError) as missing_source:
        await repo.read(source_path)
    assert missing_source.value.response.get("Error", {}).get("Code") == "NoSuchKey"
    restored_manifest = (await repo.read(".bifrost/apps.yaml")).decode("utf-8")
    assert original_name in restored_manifest
    assert original_slug in restored_manifest
    assert updated_name not in restored_manifest
    assert updated_slug not in restored_manifest
    restored = e2e_client.get(
        f"/api/applications/{original_slug}",
        headers=_platform(alice.headers),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["name"] == original_name

    async with async_session_factory() as db:
        project = await db.scalar(
            select(SolutionBuilderProject).where(
                SolutionBuilderProject.solution_id == solution_id
            )
        )
        assert project is not None
        restored_revision = await db.get(SolutionSourceRevision, project.deployed_revision_id)
        assert restored_revision is not None
        assert restored_revision.source_sha256 == baseline_revision.source_sha256
        app = await db.get(Application, app_id)
        if app is not None:
            await db.delete(app)
            await db.commit()


@pytest.mark.asyncio
async def test_platform_builder_can_apply_and_rollback_global_app_update(
    e2e_client,
    async_session_factory,
    two_platform_builders,
):
    alice, bob = two_platform_builders
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    app_id = uuid.uuid4()
    original_slug = f"e2e-global-app-{uuid.uuid4().hex[:8]}"
    original_name = "Original Global App"
    async with async_session_factory() as db:
        db.add(
            Application(
                id=app_id,
                name=original_name,
                slug=original_slug,
                description="Original app description",
                icon="app-window",
                repo_path=f"apps/{original_slug}",
                organization_id=None,
                app_model="inline_v1",
                access_level="authenticated",
                created_by="e2e@example.test",
            )
        )
        await db.commit()

    before = e2e_client.get(
        f"/api/applications/{original_slug}",
        headers=_platform(alice.headers),
    )
    assert before.status_code == 200, before.text
    updated_slug = f"{original_slug}-updated"
    updated_name = "Updated Global App"
    async with async_session_factory() as db:
        await _stage_app_update(
            db,
            solution_id,
            before=before.json(),
            name=updated_name,
            slug=updated_slug,
        )

    accepted = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/apply",
        headers=_platform(bob.headers),
    )
    assert accepted.status_code == 202, accepted.text
    completed = _poll_job(e2e_client, _platform(bob.headers), accepted.json()["job_id"])
    assert completed["status"] == "succeeded", completed
    updated = e2e_client.get(
        f"/api/applications/{updated_slug}",
        headers=_platform(alice.headers),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == updated_name
    assert updated.json()["slug"] == updated_slug
    assert updated.json()["description"] == "Updated app description"
    assert updated.json()["icon"] == "layout-dashboard"
    assert updated.json()["access_level"] == "everyone"

    rollback = e2e_client.post(
        f"{BUILDER_URL}/global-workspace/rollback",
        headers=_platform(alice.headers),
    )
    assert rollback.status_code == 202, rollback.text
    rolled_back = _poll_job(e2e_client, _platform(alice.headers), rollback.json()["job_id"])
    assert rolled_back["status"] == "succeeded", rolled_back
    restored = e2e_client.get(
        f"/api/applications/{original_slug}",
        headers=_platform(alice.headers),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["name"] == original_name
    assert restored.json()["slug"] == original_slug
    assert restored.json()["description"] == "Original app description"
    assert restored.json()["icon"] == "app-window"
    assert restored.json()["access_level"] == "authenticated"

    e2e_client.delete(f"/api/applications/{app_id}", headers=_platform(alice.headers))


def test_platform_operator_cannot_write_global_workspace(e2e_client, provider_org_user):
    response = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(provider_org_user.headers),
    )
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_revoked_platform_builder_apply_job_fails_closed(
    e2e_client,
    async_session_factory,
    platform_admin,
    platform_builder_role,
    alice_user,
):
    boundary = {"boundary_kind": "platform", "organization_id": None}
    _assign_role(e2e_client, platform_admin, platform_builder_role["id"], alice_user.user_id, boundary)
    _login_user(e2e_client, alice_user)
    created = e2e_client.post(
        f"{BUILDER_URL}/global-workspace",
        headers=_platform(alice_user.headers),
    )
    assert created.status_code == 200, created.text
    solution_id = UUID(created.json()["solution_id"])
    async with async_session_factory() as db:
        row = await _stage_create(
            db,
            solution_id,
            name=f"E2E Revoked Agent {uuid.uuid4().hex[:8]}",
        )
    _remove_role(e2e_client, platform_admin, platform_builder_role["id"], alice_user.user_id)
    _login_user(e2e_client, alice_user)
    context = _JobContext(
        job_id=uuid.uuid4(),
        lease_token=uuid.uuid4(),
        organization_id=None,
        requested_by_user_id=str(alice_user.user_id),
        requested_by_email=alice_user.email,
        requested_by_name=alice_user.name,
    )

    with pytest.raises(PlatformJobFailure) as raised:
        await run_builder_global_release_apply(
            context,
            BuilderGlobalReleaseApplyPayload(
                solution_id=solution_id,
                approved_operation_changes={
                    row.id: operation_change_review_fingerprint(row)
                },
            ),
        )
    assert raised.value.code == "builder_authorization_revoked"


def test_two_users_can_open_same_org_workspace_by_exact_boundary(
    e2e_client,
    platform_admin,
    platform_builder_role,
    alice_user,
    bob_user,
):
    org_id = alice_user.organization_id
    boundary = {"boundary_kind": "organization", "organization_id": str(org_id)}
    _assign_role(e2e_client, platform_admin, platform_builder_role["id"], alice_user.user_id, boundary)
    _assign_role(e2e_client, platform_admin, platform_builder_role["id"], bob_user.user_id, boundary)
    _login_user(e2e_client, alice_user)
    _login_user(e2e_client, bob_user)
    try:
        created = e2e_client.post(
            BUILDER_URL,
            headers=_org(alice_user.headers, org_id),
            json={
                "slug": f"org-workspace-{uuid.uuid4().hex[:8]}",
                "name": "Org Workspace",
                "target_kind": "organization",
            },
        )
        assert created.status_code == 201, created.text
        solution_id = created.json()["id"]
        opened = e2e_client.get(
            f"{BUILDER_URL}/{solution_id}",
            headers=_org(bob_user.headers, org_id),
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["target_kind"] == "organization"
        session = e2e_client.post(
            f"{BUILDER_URL}/{solution_id}/sessions",
            headers=_org(bob_user.headers, org_id),
            json={"title": "Help with org build"},
        )
        assert session.status_code == 201, session.text
    finally:
        _remove_role(e2e_client, platform_admin, platform_builder_role["id"], alice_user.user_id)
        _remove_role(e2e_client, platform_admin, platform_builder_role["id"], bob_user.user_id)
        _login_user(e2e_client, alice_user)
        _login_user(e2e_client, bob_user)
