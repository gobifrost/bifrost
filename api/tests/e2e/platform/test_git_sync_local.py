"""
Git Sync Tests — desktop-style git operations (commit, sync, fetch, etc.).

Uses local bare git repos (no GitHub needed). Tests validate the desktop-style
interface on GitHubSyncService: desktop_commit, desktop_sync, desktop_fetch,
desktop_status, desktop_diff, desktop_resolve, desktop_discard,
desktop_abort_merge, and preflight.

For "push from platform" tests, entity files are written to the persistent
dir (simulating what RepoSyncWriter does in production via dual-write to S3).

Fixtures:
- bare_repo: local bare git repo (tmp_path)
- working_clone: a working clone for committing test data
- sync_service: GitHubSyncService configured against local bare repo
"""

from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import yaml
from git import Repo
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_index import FileIndex
from src.models.orm.forms import Form
from src.models.orm.workflows import Workflow
from bifrost.manifest import read_manifest_from_dir


# =============================================================================
# Sample Content
# =============================================================================

SAMPLE_WORKFLOW_PY = """\
from bifrost import workflow

@workflow(name="Git Sync Test Workflow")
def git_sync_test_wf(message: str):
    \"\"\"A workflow for git sync testing.\"\"\"
    return {"result": message}
"""

SAMPLE_WORKFLOW_UPDATED = """\
from bifrost import workflow

@workflow(name="Git Sync Test Workflow Updated")
def git_sync_test_wf(message: str, count: int = 5):
    \"\"\"Updated workflow.\"\"\"
    return {"result": message, "count": count}
"""

SAMPLE_FORM_YAML = """\
name: Onboarding Form
description: New hire onboarding
workflow: {workflow_id}
fields:
  - name: employee_name
    type: text
    label: Employee Name
    required: true
  - name: start_date
    type: date
    label: Start Date
"""

SAMPLE_WORKFLOW_SYNTAX_ERROR = """\
from bifrost import workflow

@workflow(name="Bad Workflow")
def bad_workflow(
    # Missing closing paren and colon
    return {"oops"}
"""

SAMPLE_WORKFLOW_RUFF_ISSUES = """\
from bifrost import workflow
import os
import sys

@workflow(name="Lint Issues Workflow")
def lint_issues_wf(x):
    y = 1
    return {"result": x}
"""

SAMPLE_WORKFLOW_CLEAN = """\
from bifrost import workflow


@workflow(name="Clean Workflow")
def clean_wf(message: str) -> dict:
    \"\"\"A clean workflow.\"\"\"
    return {"result": message}
"""


def _make_manifest(
    workflows: dict | None = None,
    forms: dict | None = None,
) -> str:
    """Build a simple .bifrost/metadata.yaml string."""
    data: dict = {
        "organizations": [],
        "roles": [],
        "workflows": workflows or {},
        "forms": forms or {},
        "agents": {},
        "apps": {},
    }
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def write_entity_to_repo(persistent_dir: Path, rel_path: str, content: str) -> None:
    """Write a file to the persistent repo dir, simulating RepoSyncWriter dual-write."""
    target = persistent_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def remove_entity_from_repo(persistent_dir: Path, rel_path: str) -> None:
    """Remove a file from the persistent repo dir, simulating RepoSyncWriter deletion."""
    target = persistent_dir / rel_path
    if target.exists():
        target.unlink()


async def write_manifest_to_repo(db_session: AsyncSession, persistent_dir: Path) -> None:
    """Generate manifest from DB and write to persistent dir, simulating RepoSyncWriter.regenerate_manifest()."""
    from src.services.manifest_generator import generate_manifest
    from bifrost.manifest import write_manifest_to_dir
    manifest = await generate_manifest(db_session)
    # Filter out entities whose files don't exist in the persistent dir
    # (the test DB may contain entities from other fixtures).
    # Forms/agents carry inline content under their UUID — no companion file
    # is required, so they are NOT filtered by file existence.
    manifest.workflows = {
        k: v for k, v in manifest.workflows.items()
        if (persistent_dir / v.path).exists()
    }
    manifest.apps = {
        k: v for k, v in manifest.apps.items()
        if (persistent_dir / v.path).exists()
    }
    # Non-file entities (integrations, configs, etc.) are included as-is from DB
    write_manifest_to_dir(manifest, persistent_dir / ".bifrost")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bare_repo(tmp_path):
    """Local bare git repo with 'main' as default branch."""
    repo_path = tmp_path / "test-repo.git"
    Repo.init(str(repo_path), bare=True)
    # Set HEAD to point to main instead of master
    (repo_path / "HEAD").write_text("ref: refs/heads/main\n")
    return repo_path


@pytest.fixture
def working_clone(tmp_path, bare_repo):
    """Working clone for committing test data on 'main' branch."""
    work_path = tmp_path / "work"
    # Init a fresh repo and add origin
    repo = Repo.init(str(work_path))
    repo.create_remote("origin", str(bare_repo))
    # Create initial commit
    (work_path / ".gitkeep").touch()
    repo.index.add([".gitkeep"])
    repo.index.commit("initial")
    # Rename default branch to main and push
    repo.heads[0].rename("main")
    repo.remotes.origin.push(refspec="main:main")
    # Set upstream tracking
    repo.heads.main.set_tracking_branch(repo.remotes.origin.refs.main)
    return repo


@pytest_asyncio.fixture
async def sync_service(db_session: AsyncSession, bare_repo, tmp_path):
    """
    GitHubSyncService configured against the local bare repo.

    Uses file:// protocol to talk to the bare repo on disk.
    Replaces GitRepoManager methods with local-only versions.
    """
    import shutil
    from contextlib import asynccontextmanager
    from src.services.github_sync import GitHubSyncService

    service = GitHubSyncService(
        db=db_session,
        repo_url=f"file://{bare_repo}",
        branch="main",
    )

    # Persistent dir simulates the S3-backed persistent working tree
    persistent_dir = tmp_path / "persistent_repo"
    persistent_dir.mkdir()

    @asynccontextmanager
    async def local_checkout():
        """Simulates S3 sync down -> yield -> S3 sync up."""
        import tempfile
        work_dir = Path(tempfile.mkdtemp(prefix="bifrost-test-repo-"))
        try:
            # Sync down: copy persistent state to work dir
            if any(persistent_dir.iterdir()):
                shutil.copytree(persistent_dir, work_dir, dirs_exist_ok=True)
            yield work_dir
            # Sync up: copy work dir back to persistent state
            shutil.rmtree(persistent_dir)
            shutil.copytree(work_dir, persistent_dir)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @asynccontextmanager
    async def local_lock():
        """Simulates Redis lock -- just yields the persistent dir directly."""
        yield persistent_dir

    # Also make sync_up a no-op for lock()-based operations that call sync_up explicitly
    async def noop_sync_up(source):
        pass

    service.repo_manager.checkout = local_checkout  # type: ignore[assignment]
    service.repo_manager.lock = local_lock  # type: ignore[assignment]
    service.repo_manager.sync_up = noop_sync_up  # type: ignore[assignment]
    # Patch the module-level PERSISTENT_WORK_DIR so is_initialized checks the test dir
    import src.services.git_repo_manager as grm_mod
    grm_mod.PERSISTENT_WORK_DIR = persistent_dir
    service._persistent_dir = persistent_dir  # type: ignore[attr-defined]

    return service


@pytest_asyncio.fixture(autouse=True)
async def cleanup_test_data(db_session: AsyncSession):
    """Clean up test data between tests."""
    # Reset Redis singletons so they reconnect on the current event loop.
    # Without this, connections created on a previous test's (now-closed) loop
    # cause "Event loop is closed" errors.
    import src.core.redis_client as redis_module

    redis_module._redis_client = None

    import src.core.cache.redis_client as cache_redis_module

    cache_redis_module._shared_client = None

    yield

    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("workflows/git_sync_test%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("test_git_%"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("workflows/git_sync_test%"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("test_git_%"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like(".bifrost/%"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("forms/%"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("agents/%"))
    )

    # Clean up junction tables first (before parent entities)
    from src.models.orm.workflow_roles import WorkflowRole
    from src.models.orm.forms import FormRole
    from src.models.orm.agents import AgentRole
    from src.models.orm.app_roles import AppRole

    await db_session.execute(delete(WorkflowRole).where(WorkflowRole.assigned_by.in_(["git-sync", "test"])))
    await db_session.execute(delete(FormRole).where(FormRole.assigned_by == "git-sync"))
    await db_session.execute(delete(AgentRole).where(AgentRole.assigned_by == "git-sync"))
    await db_session.execute(delete(AppRole).where(AppRole.assigned_by == "git-sync"))

    # Clean up child records before parents (foreign key dependencies)
    from src.models.orm.agents import Agent, AgentTool
    await db_session.execute(
        delete(AgentTool).where(
            AgentTool.agent_id.in_(
                select(Agent.id).where(Agent.created_by.in_(["file_sync", "git-sync"]))
            )
        )
    )
    from src.models.orm.forms import FormField
    await db_session.execute(
        delete(FormField).where(
            FormField.form_id.in_(
                select(Form.id).where(Form.created_by.in_(["test", "git-sync"]))
            )
        )
    )

    await db_session.execute(
        delete(Form).where(Form.created_by.in_(["test", "git-sync"]))
    )
    await db_session.execute(
        delete(Agent).where(Agent.created_by.in_(["file_sync", "git-sync"]))
    )

    # Clean up new entity types created by git-sync tests
    from src.models.orm.events import EventSubscription, EventSource, ScheduleSource, WebhookSource
    from src.models.orm.config import Config
    from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
    from src.models.orm.tables import Table

    await db_session.execute(
        delete(EventSubscription).where(EventSubscription.created_by == "git-sync")
    )
    await db_session.execute(
        delete(ScheduleSource).where(
            ScheduleSource.event_source_id.in_(
                select(EventSource.id).where(EventSource.created_by == "git-sync")
            )
        )
    )
    await db_session.execute(
        delete(WebhookSource).where(
            WebhookSource.event_source_id.in_(
                select(EventSource.id).where(EventSource.created_by == "git-sync")
            )
        )
    )
    await db_session.execute(
        delete(EventSource).where(EventSource.created_by == "git-sync")
    )
    await db_session.execute(
        delete(Config).where(Config.updated_by.in_(["git-sync", "test"]))
    )
    await db_session.execute(
        delete(IntegrationConfigSchema).where(
            IntegrationConfigSchema.integration_id.in_(
                select(Integration.id).where(Integration.name.like("Test%"))
            )
        )
    )
    await db_session.execute(
        delete(IntegrationConfigSchema).where(
            IntegrationConfigSchema.integration_id.in_(
                select(Integration.id).where(Integration.name.like("Idempotent%"))
            )
        )
    )
    await db_session.execute(
        delete(IntegrationConfigSchema).where(
            IntegrationConfigSchema.integration_id.in_(
                select(Integration.id).where(Integration.name.like("%TestInteg"))
            )
        )
    )
    await db_session.execute(
        delete(IntegrationMapping).where(
            IntegrationMapping.integration_id.in_(
                select(Integration.id).where(Integration.name.like("Test%"))
            )
        )
    )
    await db_session.execute(
        delete(IntegrationMapping).where(
            IntegrationMapping.integration_id.in_(
                select(Integration.id).where(Integration.name.like("%TestInteg"))
            )
        )
    )
    await db_session.execute(
        delete(Integration).where(Integration.name.like("Test%"))
    )
    await db_session.execute(
        delete(Integration).where(Integration.name.like("Idempotent%"))
    )
    await db_session.execute(
        delete(Integration).where(Integration.name.like("%TestInteg"))
    )
    await db_session.execute(
        delete(Table).where(Table.created_by == "git-sync")
    )

    # Clean up orgs and roles last (entities FK into these)
    from src.models.orm.executions import Execution
    from src.models.orm.organizations import Organization
    from src.models.orm.users import Role

    # Executions FK into organizations (RESTRICT): other suites legitimately
    # create executions inside their created_by="test" orgs (e.g. the form
    # anchor tests stamp the execution with the FORM's org), and a single
    # surviving row makes this org sweep raise ForeignKeyViolationError —
    # which cascades as a teardown error into EVERY later test in this file.
    cohort_orgs = select(Organization.id).where(
        Organization.created_by.in_(["git-sync", "test"])
    )
    await db_session.execute(
        delete(Execution).where(Execution.organization_id.in_(cohort_orgs))
    )
    await db_session.execute(delete(Organization).where(Organization.created_by.in_(["git-sync", "test"])))
    await db_session.execute(delete(Role).where(Role.created_by == "git-sync"))
    await db_session.commit()


# =============================================================================
# Platform → Empty Repo (Initial Export)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPushToEmptyRepo:
    """Push platform state to an empty repo (initial git connect)."""

    async def test_push_to_empty_repo(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        tmp_path,
    ):
        """
        Create entities in DB, write file to repo dir, commit + push →
        clone and verify workflow .py files, manifest.
        """
        # Create a workflow in DB
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Git Sync Test Workflow",
            function_name="git_sync_test_wf",
            path="workflows/git_sync_test_wf.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write workflow file to persistent dir (simulates RepoSyncWriter)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_wf.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit + push
        commit_result = await sync_service.desktop_commit("initial commit")
        assert commit_result.success is True
        assert commit_result.files_committed > 0

        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success is True
        assert sync_result.pushed_commits > 0

        # Verify by cloning the repo and checking files
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")

        # Workflow .py file should exist
        wf_file = verify_path / "workflows" / "git_sync_test_wf.py"
        assert wf_file.exists(), "Workflow .py file should exist in repo"

        # Split manifest files should exist (not legacy metadata.yaml)
        bifrost_dir = verify_path / ".bifrost"
        assert bifrost_dir.exists(), "Manifest directory should exist in repo"
        assert (bifrost_dir / "workflows.yaml").exists(), "Split workflows.yaml should exist"
        assert not (bifrost_dir / "metadata.yaml").exists(), "Legacy metadata.yaml should not exist"

    async def test_push_generates_manifest(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        tmp_path,
    ):
        """Commit + push → .bifrost/ split files exist with correct entries."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Manifest Test Workflow",
            function_name="manifest_test_wf",
            path="workflows/git_sync_test_manifest.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write workflow file to persistent dir (simulates RepoSyncWriter)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_manifest.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit + push
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Clone and verify manifest
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")

        manifest = read_manifest_from_dir(verify_path / ".bifrost")

        # Workflow should be in manifest
        assert len(manifest.workflows) >= 1
        found = False
        for name, mwf in manifest.workflows.items():
            if mwf.id == str(wf_id):
                found = True
                assert mwf.path == "workflows/git_sync_test_manifest.py"
                assert mwf.function_name == "manifest_test_wf"
                break
        assert found, f"Workflow {wf_id} should be in manifest"


# =============================================================================
# Platform → Existing Repo (Incremental Push)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestIncrementalPush:
    """Push changes to a repo that already has content."""

    async def test_push_updates_modified_files(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        tmp_path,
    ):
        """Commit+push, modify workflow file, commit+push again → repo has updated content."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Git Sync Test Workflow",
            function_name="git_sync_test_wf",
            path="workflows/git_sync_test_incremental.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write workflow file and first commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_incremental.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        result1 = await sync_service.desktop_commit("initial commit")
        assert result1.success is True
        await sync_service.desktop_sync(confirm_deletes=True)

        # Modify the workflow name in DB and update file in persistent dir
        wf.name = "Git Sync Test Workflow Updated"
        wf.description = "Updated description"
        await db_session.commit()
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_incremental.py",
            SAMPLE_WORKFLOW_UPDATED,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Second commit+push
        result2 = await sync_service.desktop_commit("update workflow")
        assert result2.success is True
        await sync_service.desktop_sync(confirm_deletes=True)

        # Verify updated content
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")
        manifest = read_manifest_from_dir(verify_path / ".bifrost")
        # Manifest should reflect the update
        found = False
        for name, mwf in manifest.workflows.items():
            if mwf.id == str(wf_id):
                found = True
                break
        assert found

    async def test_push_deletes_removed_files(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        tmp_path,
    ):
        """Commit+push, delete workflow file, commit+push again → file gone from repo."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Git Sync Delete Test",
            function_name="git_sync_delete_wf",
            path="workflows/git_sync_test_delete.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and first commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_delete.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Deactivate the workflow and remove file from persistent dir
        wf.is_active = False
        await db_session.commit()
        remove_entity_from_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_delete.py",
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Second commit+push
        await sync_service.desktop_commit("delete workflow")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Verify file is gone
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")
        wf_file = verify_path / "workflows" / "git_sync_test_delete.py"
        assert not wf_file.exists(), "Deleted workflow file should be gone from repo"


# =============================================================================
# Repo → Platform (Sync / Pull)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPull:
    """Sync (pull) changes from repo into platform."""

    async def test_pull_new_workflow(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Commit .py with @workflow to repo, desktop_sync → workflows table + file_index populated."""
        work_dir = Path(working_clone.working_dir)

        # Create workflow file in the repo
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        wf_file = wf_dir / "git_sync_test_pulled.py"
        wf_file.write_text(SAMPLE_WORKFLOW_PY)

        # Create a manifest pointing to this file
        wf_id = str(uuid4())
        manifest_content = _make_manifest(
            workflows={
                "Git Sync Test Workflow": {
                    "id": wf_id,
                    "path": "workflows/git_sync_test_pulled.py",
                    "function_name": "git_sync_test_wf",
                    "type": "workflow",
                }
            }
        )
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        # Commit and push
        working_clone.index.add([
            "workflows/git_sync_test_pulled.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("add test workflow")
        working_clone.remotes.origin.push()

        # Pull into platform
        result = await sync_service.desktop_sync(confirm_deletes=True)

        assert result.success is True
        assert result.entities_imported > 0

        # Verify workflow in DB
        wf_result = await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )
        wf = wf_result.scalar_one_or_none()
        assert wf is not None
        assert wf.name == "Git Sync Test Workflow"
        assert wf.function_name == "git_sync_test_wf"

        # Verify parameters_schema enriched by WorkflowIndexer
        assert len(wf.parameters_schema) == 1
        assert wf.parameters_schema[0]["name"] == "message"
        assert wf.parameters_schema[0]["required"] is True

        # Verify file_index populated
        fi_result = await db_session.execute(
            select(FileIndex).where(
                FileIndex.path == "workflows/git_sync_test_pulled.py"
            )
        )
        fi = fi_result.scalar_one_or_none()
        assert fi is not None
        assert "@workflow" in fi.content

    async def test_pull_new_form(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Inline form content in manifest → desktop_sync → forms table populated."""
        work_dir = Path(working_clone.working_dir)

        form_id = str(uuid4())
        wf_id = str(uuid4())

        # Create manifest with inline form content (no separate .form.yaml file)
        manifest_content = _make_manifest(
            forms={
                "Onboarding Form": {
                    "id": form_id,
                    "name": "Onboarding Form",
                    "workflow_id": wf_id,
                }
            }
        )
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("add test form")
        working_clone.remotes.origin.push()

        # Pull
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify form in DB
        form_result = await db_session.execute(
            select(Form).where(Form.id == form_id)
        )
        form = form_result.scalar_one_or_none()
        assert form is not None
        assert form.name == "Onboarding Form"

    async def test_pull_new_form_with_fields(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Form pulled from repo with inline content should have field definitions."""
        from src.models.orm.forms import Form, FormField
        from src.models.orm.workflows import Workflow

        work_dir = Path(working_clone.working_dir)

        # Create a workflow to be referenced
        wf_id = uuid4()
        db_session.add(Workflow(
            id=wf_id,
            name="form_test_workflow",
            path="workflows/form_test.py",
            function_name="form_test_workflow",
            is_active=True,
        ))
        await db_session.flush()

        form_id = uuid4()

        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "metadata.yaml").write_text(f"""forms:
  Test Form With Fields:
    id: "{form_id}"
    name: Test Form With Fields
    workflow_id: "{wf_id}"
    description: Form with field definitions
    organization_id: null
    roles: []
    form_schema:
      fields:
      - name: email
        type: text
        label: Email Address
        required: true
      - name: count
        type: number
        label: Count
        required: false
        default_value: 5
workflows: {{}}
agents: {{}}
apps: {{}}
organizations: []
roles: []
""")

        working_clone.index.add([".bifrost/"])
        working_clone.index.commit("Add form with fields")
        working_clone.remotes.origin.push()

        # Pull into platform
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify form was created
        form = await db_session.get(Form, form_id)
        assert form is not None
        assert form.name == "Test Form With Fields"
        assert str(form.workflow_id) == str(wf_id)

        # Verify fields were imported
        from sqlalchemy import select
        fields = (await db_session.execute(
            select(FormField).where(FormField.form_id == form_id).order_by(FormField.position)
        )).scalars().all()
        assert len(fields) == 2
        assert fields[0].name == "email"
        assert fields[0].type == "text"
        assert fields[0].required is True
        assert fields[1].name == "count"
        assert fields[1].default_value == 5

    async def test_pull_new_agent_with_tools(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Agent pulled from repo should have tool associations."""
        from src.models.orm.agents import Agent
        from src.models.orm.workflows import Workflow

        work_dir = Path(working_clone.working_dir)

        # Create a workflow to be referenced as a tool
        wf_id = uuid4()
        db_session.add(Workflow(
            id=wf_id,
            name="agent_tool_workflow",
            path="workflows/agent_tool.py",
            function_name="agent_tool_workflow",
            is_active=True,
        ))
        await db_session.flush()

        agent_id = uuid4()

        # Write workflow file + manifest with inline agent content
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "agent_tool.py").write_text(SAMPLE_WORKFLOW_PY)

        manifest_dir = work_dir / ".bifrost"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "metadata.yaml").write_text(f"""agents:
  Test Agent With Tools:
    id: "{agent_id}"
    name: Test Agent With Tools
    description: Agent with tool associations
    system_prompt: You are a test agent.
    channels:
    - chat
    tool_ids:
    - "{wf_id}"
    organization_id: null
    roles: []
workflows:
  agent_tool_workflow:
    id: "{wf_id}"
    path: workflows/agent_tool.py
    function_name: agent_tool_workflow
forms: {{}}
apps: {{}}
organizations: []
roles: []
""")

        # Commit to repo
        working_clone.index.add(["workflows/", ".bifrost/"])
        working_clone.index.commit("Add agent with tools")
        working_clone.remotes.origin.push()

        # Pull into platform
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify agent was created with tool association
        agent = await db_session.get(Agent, agent_id)
        assert agent is not None
        assert agent.name == "Test Agent With Tools"
        assert agent.channels == ["chat"]

        # Verify tool association
        from sqlalchemy import select
        from src.models.orm.agents import AgentTool
        tools = (await db_session.execute(
            select(AgentTool).where(AgentTool.agent_id == agent_id)
        )).scalars().all()
        assert len(tools) == 1
        assert tools[0].workflow_id == wf_id

    async def test_pull_modified_workflow(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
        bare_repo,
        tmp_path,
    ):
        """Commit+push, modify in repo, desktop_sync → file_index updated."""
        # Create workflow in platform
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Git Sync Test Workflow",
            function_name="git_sync_test_wf",
            path="workflows/git_sync_test_modified.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file to persistent dir and commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_modified.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Modify in repo via working clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_modified.py"
        wf_file.write_text(SAMPLE_WORKFLOW_UPDATED)
        working_clone.index.add(["workflows/git_sync_test_modified.py"])
        working_clone.index.commit("update workflow")
        working_clone.remotes.origin.push()

        # Pull into platform
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify file_index has updated content
        fi_result = await db_session.execute(
            select(FileIndex).where(
                FileIndex.path == "workflows/git_sync_test_modified.py"
            )
        )
        fi = fi_result.scalar_one_or_none()
        assert fi is not None
        assert "Updated" in fi.content

    async def test_pull_deleted_file(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Commit+push, delete in repo, desktop_sync → entity deactivated, file_index cleaned."""
        # Create workflow in platform
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Git Sync Delete Pull Test",
            function_name="git_sync_delete_pull_wf",
            path="workflows/git_sync_test_del_pull.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_del_pull.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Delete in repo via working clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_del_pull.py"
        if wf_file.exists():
            wf_file.unlink()
        working_clone.index.remove(["workflows/git_sync_test_del_pull.py"])
        working_clone.index.commit("delete workflow")
        working_clone.remotes.origin.push()

        # Pull
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Workflow should be hard-deleted (git history is the undo mechanism)
        wf_result = await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )
        assert wf_result.scalar_one_or_none() is None

        # file_index entry should be removed
        fi_result = await db_session.execute(
            select(FileIndex).where(
                FileIndex.path == "workflows/git_sync_test_del_pull.py"
            )
        )
        assert fi_result.scalar_one_or_none() is None


# =============================================================================
# Renames
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRenames:
    """Renames are detected correctly during sync."""

    async def test_rename_in_repo_detected_on_pull(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Commit+push, rename file in repo, desktop_sync → platform path updated."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Rename Test Workflow",
            function_name="rename_test_wf",
            path="workflows/git_sync_test_rename_old.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_rename_old.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Rename in repo via working clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        old_file = work_dir / "workflows" / "git_sync_test_rename_old.py"
        new_file = work_dir / "workflows" / "git_sync_test_rename_new.py"
        if old_file.exists():
            new_file.write_text(old_file.read_text())
            old_file.unlink()

        # Update manifest with new path (split format: workflows.yaml)
        wf_manifest = work_dir / ".bifrost" / "workflows.yaml"
        if wf_manifest.exists():
            wf_data_yaml = yaml.safe_load(wf_manifest.read_text())
            for name, wf_data in wf_data_yaml.get("workflows", {}).items():
                if wf_data.get("id") == str(wf_id):
                    wf_data["path"] = "workflows/git_sync_test_rename_new.py"
            wf_manifest.write_text(
                yaml.dump(wf_data_yaml, default_flow_style=False, sort_keys=False)
            )

        working_clone.index.add([
            "workflows/git_sync_test_rename_new.py",
            ".bifrost/workflows.yaml",
        ])
        working_clone.index.remove(["workflows/git_sync_test_rename_old.py"])
        working_clone.index.commit("rename workflow")
        working_clone.remotes.origin.push()

        # Pull
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Workflow path should be updated
        await db_session.refresh(wf)
        assert wf.path == "workflows/git_sync_test_rename_new.py"

    async def test_rename_in_platform_pushed(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
        bare_repo,
        tmp_path,
    ):
        """Commit+push, rename in platform, commit+push → repo has new path."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Platform Rename Test",
            function_name="platform_rename_wf",
            path="workflows/git_sync_test_plat_old.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_plat_old.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Rename in platform: remove old file, write new file, update DB path
        remove_entity_from_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_plat_old.py",
        )
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_plat_new.py",
            SAMPLE_WORKFLOW_PY,
        )
        wf.path = "workflows/git_sync_test_plat_new.py"
        await db_session.commit()
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit+push again
        await sync_service.desktop_commit("rename workflow")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Verify new path in repo
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")
        old_file = verify_path / "workflows" / "git_sync_test_plat_old.py"
        new_file = verify_path / "workflows" / "git_sync_test_plat_new.py"
        assert not old_file.exists(), "Old path should be gone"
        assert new_file.exists(), "New path should exist"


# =============================================================================
# Conflicts
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestConflicts:
    """Conflict detection when both sides modify."""

    async def test_both_modified_is_conflict(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Commit+push, modify both sides, desktop_sync → conflict detected."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Conflict Test Workflow",
            function_name="conflict_test_wf",
            path="workflows/git_sync_test_conflict.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_conflict.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Modify in repo via working clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_conflict.py"
        wf_file.write_text(SAMPLE_WORKFLOW_UPDATED)
        working_clone.index.add(["workflows/git_sync_test_conflict.py"])
        working_clone.index.commit("modify workflow in repo")
        working_clone.remotes.origin.push()

        # Modify in platform (different change) — write different content to persistent dir
        wf.name = "Conflict Test Workflow Platform Modified"
        await db_session.commit()
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_conflict.py",
            SAMPLE_WORKFLOW_CLEAN,  # Different content from repo change
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit locally (creates divergent history)
        await sync_service.desktop_commit("modify workflow in platform")

        # Pull should detect conflict since both sides diverged
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success is False
        assert len(sync_result.conflicts) > 0

        conflict_paths = [c.path for c in sync_result.conflicts]
        # The conflict could be on the workflow file or any manifest split file
        assert any(
            "git_sync_test_conflict" in p or ".bifrost/" in p
            for p in conflict_paths
        ), f"Expected conflict on test file, got: {conflict_paths}"

    async def test_platform_modified_repo_deleted_is_conflict(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Commit+push, modify platform + delete in repo → conflict on pull."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Delete Conflict Test",
            function_name="delete_conflict_wf",
            path="workflows/git_sync_test_del_conflict.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_del_conflict.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Delete in repo via working clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_del_conflict.py"
        if wf_file.exists():
            wf_file.unlink()
        working_clone.index.remove(["workflows/git_sync_test_del_conflict.py"])
        working_clone.index.commit("delete workflow in repo")
        working_clone.remotes.origin.push()

        # Modify in platform (creates divergent local commit)
        wf.name = "Delete Conflict Test Modified"
        wf.description = "Modified after repo deletion"
        await db_session.commit()
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_del_conflict.py",
            SAMPLE_WORKFLOW_UPDATED,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("modify workflow in platform")

        # Pull should detect conflict (delete vs modify)
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        # Either an explicit conflict or a failed merge
        has_conflict = len(sync_result.conflicts) > 0
        has_error = sync_result.success is False
        assert has_conflict or has_error, (
            f"Expected conflict or error. conflicts={sync_result.conflicts}, "
            f"success={sync_result.success}, error={sync_result.error}"
        )


# =============================================================================
# Round-Trip
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRoundTrip:
    """Entity identity preserved through push/pull cycles."""

    async def test_workflow_survives_round_trip(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Create → commit+push → modify in repo → desktop_sync → ID preserved, content matches."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Round Trip Test",
            function_name="round_trip_wf",
            path="workflows/git_sync_test_roundtrip.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and commit+push
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_roundtrip.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Modify in repo via working clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_roundtrip.py"
        wf_file.write_text(SAMPLE_WORKFLOW_UPDATED)
        working_clone.index.add(["workflows/git_sync_test_roundtrip.py"])
        working_clone.index.commit("update workflow content")
        working_clone.remotes.origin.push()

        # Pull
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # ID should be preserved
        await db_session.refresh(wf)
        assert wf.id == wf_id
        assert wf.is_active is True

        # file_index should have updated content
        fi_result = await db_session.execute(
            select(FileIndex).where(
                FileIndex.path == "workflows/git_sync_test_roundtrip.py"
            )
        )
        fi = fi_result.scalar_one_or_none()
        assert fi is not None
        assert "Updated" in fi.content

    async def test_form_uuid_refs_round_trip(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
        bare_repo,
        tmp_path,
    ):
        """Create workflow + form → commit+push → verify form yaml has UUID ref → pull → UUID preserved."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Form Ref Workflow",
            function_name="form_ref_wf",
            path="workflows/git_sync_test_formref.py",
            is_active=True,
        )
        db_session.add(wf)

        form_id = uuid4()
        form = Form(
            id=form_id,
            name="Form Ref Test",
            workflow_id=str(wf_id),
            is_active=True,
            created_by="test",
        )
        db_session.add(form)
        await db_session.commit()

        # Write workflow file + manifest (form content is inline in the manifest)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_formref.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit + push
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Verify the inline manifest entry contains the workflow UUID reference
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")

        forms_manifest = verify_path / ".bifrost" / "forms.yaml"
        assert forms_manifest.exists(), "forms.yaml should be in repo"
        manifest_text = forms_manifest.read_text()
        assert str(form_id) in manifest_text, "Manifest should contain form UUID"
        assert str(wf_id) in manifest_text, "Manifest should contain workflow UUID reference"

        # Pull back
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # UUID should be preserved
        await db_session.refresh(form)
        assert str(form.workflow_id) == str(wf_id)

    async def test_agent_with_tools_survives_round_trip(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
        bare_repo,
        tmp_path,
    ):
        """Create agent with tools -> commit+push -> pull back -> tools preserved."""
        from src.models.orm.agents import Agent, AgentTool
        from src.models.orm.workflows import Workflow as WfModel

        # Create workflow (tool) and agent in DB
        wf_id = uuid4()
        db_session.add(WfModel(
            id=wf_id,
            name="RT Agent Tool WF",
            path="workflows/git_sync_test_rt_agent_tool.py",
            function_name="rt_agent_tool_wf",
            is_active=True,
        ))

        agent_id = uuid4()
        agent = Agent(
            id=agent_id,
            name="Round Trip Agent",
            system_prompt="Test agent for round trip",
            channels=["chat"],
            is_active=True,
            created_by="file_sync",
        )
        db_session.add(agent)
        await db_session.flush()

        # Add tool association
        db_session.add(AgentTool(agent_id=agent_id, workflow_id=wf_id))
        await db_session.commit()

        # Write workflow file + manifest (agent content is inline in the manifest)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_rt_agent_tool.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit + push
        commit_result = await sync_service.desktop_commit("initial commit with agent")
        assert commit_result.success is True
        await sync_service.desktop_sync(confirm_deletes=True)

        # Verify the inline manifest entry contains the agent and its tool UUID
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")
        agents_manifest = verify_path / ".bifrost" / "agents.yaml"
        assert agents_manifest.exists(), "agents.yaml should be in repo"
        manifest_text = agents_manifest.read_text()
        assert str(agent_id) in manifest_text, "Manifest should contain agent UUID"
        assert str(wf_id) in manifest_text, "Manifest should contain tool UUID"

        # Pull back
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify agent still exists with tools
        await db_session.refresh(agent)
        assert agent.name == "Round Trip Agent"
        assert agent.channels == ["chat"]

        tools = (await db_session.execute(
            select(AgentTool).where(AgentTool.agent_id == agent_id)
        )).scalars().all()
        assert len(tools) >= 1, f"Agent should still have tool associations, got {len(tools)}"
        assert any(t.workflow_id == wf_id for t in tools)

    async def test_form_with_fields_survives_round_trip(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
        bare_repo,
        tmp_path,
    ):
        """Create form with fields -> commit+push -> pull back -> fields preserved."""
        from src.models.orm.forms import FormField
        from src.models.orm.workflows import Workflow as WfModel

        # Create workflow and form in DB
        wf_id = uuid4()
        db_session.add(WfModel(
            id=wf_id,
            name="RT Form WF",
            path="workflows/git_sync_test_rt_form.py",
            function_name="rt_form_wf",
            is_active=True,
        ))

        form_id = uuid4()
        form = Form(
            id=form_id,
            name="Round Trip Form",
            description="Test form for round trip",
            workflow_id=str(wf_id),
            is_active=True,
            created_by="test",
        )
        db_session.add(form)
        await db_session.flush()

        # Add form fields
        db_session.add(FormField(
            form_id=form_id,
            name="email",
            type="text",
            label="Email Address",
            required=True,
            position=0,
        ))
        db_session.add(FormField(
            form_id=form_id,
            name="count",
            type="number",
            label="Count",
            required=False,
            default_value=5,
            position=1,
        ))
        await db_session.commit()

        # Write workflow file + manifest (form content is inline in the manifest)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_rt_form.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit + push
        commit_result = await sync_service.desktop_commit("initial commit with form")
        assert commit_result.success is True
        await sync_service.desktop_sync(confirm_deletes=True)

        # Verify the inline form manifest entry round-trips
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")
        forms_manifest = verify_path / ".bifrost" / "forms.yaml"
        assert forms_manifest.exists(), "forms.yaml should be in repo"
        manifest_text = forms_manifest.read_text()
        assert str(form_id) in manifest_text
        assert "email" in manifest_text and "count" in manifest_text

        # Pull back
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify form still exists with fields
        await db_session.refresh(form)
        assert form.name == "Round Trip Form"
        assert str(form.workflow_id) == str(wf_id)

        fields = (await db_session.execute(
            select(FormField).where(FormField.form_id == form_id).order_by(FormField.position)
        )).scalars().all()
        assert len(fields) == 2, f"Form should have 2 fields, got {len(fields)}"
        assert fields[0].name == "email"
        assert fields[0].required is True
        assert fields[1].name == "count"
        assert fields[1].default_value == 5


# =============================================================================
# Orphan Detection
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestOrphanDetection:
    """Orphan warnings when referenced workflows are deleted."""

    async def test_orphan_warning_on_workflow_delete(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Commit+push workflow + form, delete workflow in repo, pull → preflight warns about orphan on next commit attempt."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Orphan Source Workflow",
            function_name="orphan_source_wf",
            path="workflows/git_sync_test_orphan.py",
            is_active=True,
        )
        db_session.add(wf)

        form_id = uuid4()
        form = Form(
            id=form_id,
            name="Orphan Form Test",
            workflow_id=str(wf_id),
            is_active=True,
            created_by="test",
        )
        db_session.add(form)
        await db_session.commit()

        # Write workflow file + manifest (form content is inline)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_orphan.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Delete workflow from repo via working clone (keep form)
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_orphan.py"
        if wf_file.exists():
            wf_file.unlink()
        # Update manifest to remove the workflow entry (split format)
        wf_manifest = work_dir / ".bifrost" / "workflows.yaml"
        if wf_manifest.exists():
            wf_manifest.unlink()
        working_clone.git.add(A=True)
        working_clone.index.commit("delete workflow, leave form")
        working_clone.remotes.origin.push()

        # Pull the changes from repo
        await sync_service.desktop_sync(confirm_deletes=True)

        # Now run preflight — orphan detection happens here
        # The form still references a workflow UUID that is no longer in the manifest
        preflight = await sync_service.preflight()

        # Check preflight for orphan warnings
        has_orphan_warning = any(
            i.category == "orphan" for i in preflight.issues
        )

        assert has_orphan_warning, (
            f"Preflight should warn about orphaned form referencing deleted workflow. "
            f"Issues: {preflight.issues}"
        )


# =============================================================================
# Preflight Validation
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPreflightValidation:
    """Preflight checks validate repo health before sync."""

    async def test_preflight_catches_syntax_error(
        self,
        sync_service,
        working_clone,
    ):
        """Commit .py with syntax error → preflight returns error."""
        work_dir = Path(working_clone.working_dir)

        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "test_git_bad_syntax.py").write_text(SAMPLE_WORKFLOW_SYNTAX_ERROR)

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "metadata.yaml").write_text(_make_manifest(
            workflows={
                "Bad Workflow": {
                    "id": str(uuid4()),
                    "path": "workflows/test_git_bad_syntax.py",
                    "function_name": "bad_workflow",
                    "type": "workflow",
                }
            }
        ))

        working_clone.index.add([
            "workflows/test_git_bad_syntax.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("add bad workflow")
        working_clone.remotes.origin.push()

        preflight = await sync_service.preflight()

        assert preflight.valid is False
        syntax_issues = [i for i in preflight.issues if i.category == "syntax"]
        assert len(syntax_issues) > 0, "Should have syntax error issues"

    async def test_preflight_catches_unresolved_ref(
        self,
        sync_service,
        working_clone,
    ):
        """Commit form manifest entry referencing nonexistent UUID → preflight returns error."""
        work_dir = Path(working_clone.working_dir)

        fake_wf_id = str(uuid4())
        form_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "metadata.yaml").write_text(_make_manifest(
            forms={
                "Onboarding Form": {
                    "id": form_id,
                    "name": "Onboarding Form",
                    "workflow_id": fake_wf_id,
                }
            }
            # No workflow in manifest — ref is unresolved
        ))

        working_clone.index.add([
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("add form with bad ref")
        working_clone.remotes.origin.push()

        preflight = await sync_service.preflight()

        assert preflight.valid is False
        ref_issues = [i for i in preflight.issues if i.category == "ref"]
        assert len(ref_issues) > 0, "Should have unresolved ref issues"

    async def test_preflight_catches_ruff_violations(
        self,
        sync_service,
        working_clone,
    ):
        """Commit .py with linting issues → preflight returns warnings."""
        work_dir = Path(working_clone.working_dir)

        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "test_git_lint.py").write_text(SAMPLE_WORKFLOW_RUFF_ISSUES)

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "metadata.yaml").write_text(_make_manifest(
            workflows={
                "Lint Issues Workflow": {
                    "id": str(uuid4()),
                    "path": "workflows/test_git_lint.py",
                    "function_name": "lint_issues_wf",
                    "type": "workflow",
                }
            }
        ))

        working_clone.index.add([
            "workflows/test_git_lint.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("add lint-issues workflow")
        working_clone.remotes.origin.push()

        preflight = await sync_service.preflight()

        lint_issues = [i for i in preflight.issues if i.category == "lint"]
        assert len(lint_issues) > 0, "Should have lint warning issues"
        # Lint issues should be warnings, not errors
        assert all(i.severity == "warning" for i in lint_issues)

    async def test_preflight_passes_clean_repo(
        self,
        sync_service,
        working_clone,
    ):
        """Valid repo → preflight returns clean."""
        work_dir = Path(working_clone.working_dir)

        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "test_git_clean.py").write_text(SAMPLE_WORKFLOW_CLEAN)

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "metadata.yaml").write_text(_make_manifest(
            workflows={
                "Clean Workflow": {
                    "id": str(uuid4()),
                    "path": "workflows/test_git_clean.py",
                    "function_name": "clean_wf",
                    "type": "workflow",
                }
            }
        ))

        working_clone.index.add([
            "workflows/test_git_clean.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("add clean workflow")
        working_clone.remotes.origin.push()

        preflight = await sync_service.preflight()

        assert preflight.valid is True
        errors = [i for i in preflight.issues if i.severity == "error"]
        assert len(errors) == 0, f"Should have no errors, got: {errors}"


# =============================================================================
# Split Manifest Format
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestSplitManifestFormat:
    """Verify split manifest files work through commit/pull cycle."""

    async def test_push_creates_split_files(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        tmp_path,
    ):
        """Commit+push → .bifrost/ contains per-entity-type YAML files, not metadata.yaml."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Split Manifest Test WF",
            function_name="split_manifest_wf",
            path="workflows/git_sync_test_split.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_split.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Clone and verify split files
        verify_path = tmp_path / "verify"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")

        bifrost_dir = verify_path / ".bifrost"
        assert (bifrost_dir / "workflows.yaml").exists(), "workflows.yaml should exist"
        assert not (bifrost_dir / "metadata.yaml").exists(), "legacy metadata.yaml should not exist"

        # Verify content
        wf_data = yaml.safe_load((bifrost_dir / "workflows.yaml").read_text())
        assert "workflows" in wf_data
        found = any(
            wf_entry.get("id") == str(wf_id)
            for wf_entry in wf_data["workflows"].values()
        )
        assert found, f"Workflow {wf_id} should be in workflows.yaml"

    async def test_pull_integration_from_manifest(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull manifest with integration → creates Integration, config_schema, mappings in DB."""
        from src.models.orm.integrations import Integration, IntegrationConfigSchema

        work_dir = Path(working_clone.working_dir)
        integ_id = str(uuid4())

        # Write split manifest with integration
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "TestInteg": {
                    "id": integ_id,
                    "entity_id": "tenant_id",
                    "entity_id_name": "Tenant",
                    "config_schema": [
                        {"key": "api_url", "type": "string", "required": True, "position": 0},
                        {"key": "api_key", "type": "secret", "required": True, "position": 1},
                    ],
                    "mappings": [],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("add integration")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify integration in DB
        from uuid import UUID as UUIDType
        integ = await db_session.get(Integration, UUIDType(integ_id))
        assert integ is not None
        assert integ.name == "TestInteg"
        assert integ.entity_id == "tenant_id"

        # Verify config schema
        cs_result = await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == UUIDType(integ_id)
            ).order_by(IntegrationConfigSchema.position)
        )
        schemas = cs_result.scalars().all()
        assert len(schemas) == 2
        assert schemas[0].key == "api_url"
        assert schemas[0].type == "string"
        assert schemas[1].key == "api_key"
        assert schemas[1].type == "secret"

    async def test_pull_config_from_manifest(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull manifest with configs → creates Config entries in DB."""
        from src.models.orm.config import Config

        work_dir = Path(working_clone.working_dir)
        config_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "configs.yaml").write_text(yaml.dump({
            "configs": {
                "app/api_url": {
                    "id": config_id,
                    "key": "app/api_url",
                    "config_type": "string",
                    "description": "API Base URL",
                    "value": "https://api.example.com",
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/configs.yaml"])
        working_clone.index.commit("add config")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify config in DB
        from uuid import UUID as UUIDType
        cfg = await db_session.get(Config, UUIDType(config_id))
        assert cfg is not None
        assert cfg.key == "app/api_url"
        assert cfg.config_type == "string"
        assert cfg.value == "https://api.example.com"

    async def test_pull_integration_config_links_config_schema_id(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Integration configs imported from split manifests must link to
        their IntegrationConfigSchema rows and keep that link after export.
        """
        from uuid import UUID as UUIDType

        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration, IntegrationConfigSchema

        work_dir = Path(working_clone.working_dir)
        integ_id = str(uuid4())
        config_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "TestIntegConfigSchemaLink": {
                    "id": integ_id,
                    "config_schema": [
                        {
                            "key": "EXAMPLE_TOKEN",
                            "type": "secret",
                            "required": True,
                            "position": 0,
                        },
                    ],
                },
            },
        }, default_flow_style=False))
        (bifrost_dir / "configs.yaml").write_text(yaml.dump({
            "configs": {
                "EXAMPLE_TOKEN": {
                    "id": config_id,
                    "integration_id": integ_id,
                    "key": "EXAMPLE_TOKEN",
                    "config_type": "secret",
                    "value": {"value": ""},
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([
            ".bifrost/integrations.yaml",
            ".bifrost/configs.yaml",
        ])
        working_clone.index.commit("add integration config with schema")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        schema = (
            await db_session.execute(
                select(IntegrationConfigSchema).where(
                    IntegrationConfigSchema.integration_id == UUIDType(integ_id),
                    IntegrationConfigSchema.key == "EXAMPLE_TOKEN",
                )
            )
        ).scalar_one()
        cfg = await db_session.get(Config, UUIDType(config_id))

        assert cfg is not None
        assert cfg.config_schema_id == schema.id

        # Existing secret configs keep their value during import, but sync
        # must still repair a missing schema link.
        cfg.config_schema_id = None
        await db_session.commit()
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True
        await db_session.refresh(cfg)
        assert cfg.value == {"value": ""}
        assert cfg.config_schema_id == schema.id

        # Export should preserve the portable shape: schema lives under the
        # integration and the config references that integration by UUID.
        commit_result = await sync_service.desktop_commit("round trip integration config")
        assert commit_result.success is True
        exported = read_manifest_from_dir(sync_service._persistent_dir / ".bifrost")
        exported_integ = exported.integrations[integ_id]
        assert [cs.key for cs in exported_integ.config_schema] == ["EXAMPLE_TOKEN"]
        exported_cfg = exported.configs[config_id]
        assert exported_cfg.integration_id == integ_id
        assert exported_cfg.key == "EXAMPLE_TOKEN"

        # Re-import the exported manifest into an empty DB state; the schema
        # linkage must be restored from integration_id + key.
        await db_session.execute(delete(Config).where(Config.id == UUIDType(config_id)))
        await db_session.execute(
            delete(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == UUIDType(integ_id)
            )
        )
        await db_session.execute(
            delete(Integration).where(Integration.id == UUIDType(integ_id))
        )
        await db_session.commit()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        roundtrip_schema = (
            await db_session.execute(
                select(IntegrationConfigSchema).where(
                    IntegrationConfigSchema.integration_id == UUIDType(integ_id),
                    IntegrationConfigSchema.key == "EXAMPLE_TOKEN",
                )
            )
        ).scalar_one()
        roundtrip_cfg = await db_session.get(Config, UUIDType(config_id))
        assert roundtrip_cfg is not None
        assert roundtrip_cfg.config_schema_id == roundtrip_schema.id

    async def test_pull_table_from_manifest(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull manifest with table → creates Table in DB with schema."""
        from src.models.orm.tables import Table

        work_dir = Path(working_clone.working_dir)
        table_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "tables.yaml").write_text(yaml.dump({
            "tables": {
                "ticket_cache": {
                    "id": table_id,
                    "description": "Cached tickets",
                    "schema": {
                        "columns": [
                            {"name": "ticket_id", "type": "string"},
                            {"name": "subject", "type": "string"},
                        ]
                    },
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/tables.yaml"])
        working_clone.index.commit("add table")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify table in DB
        from uuid import UUID as UUIDType
        table = await db_session.get(Table, UUIDType(table_id))
        assert table is not None
        assert table.name == "ticket_cache"
        assert table.description == "Cached tickets"
        assert table.schema is not None
        assert len(table.schema["columns"]) == 2

    async def test_pull_custom_claim_from_manifest(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull manifest with custom claim → creates CustomClaim in DB."""
        from uuid import UUID as UUIDType

        from src.models.orm.custom_claims import CustomClaim

        work_dir = Path(working_clone.working_dir)
        org_id = str(uuid4())
        claim_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": org_id, "name": "ClaimsImportOrg"}],
        }, default_flow_style=False))
        (bifrost_dir / "claims.yaml").write_text(yaml.dump({
            "claims": {
                claim_id: {
                    "id": claim_id,
                    "name": "allowed_campus_ids",
                    "description": "Campuses this user can read",
                    "organization_id": org_id,
                    "type": "list",
                    "query": {
                        "table": "user_campus_access",
                        "where": {"eq": [{"row": "user_id"}, {"user": "user_id"}]},
                        "select": "campus_id",
                    },
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/organizations.yaml", ".bifrost/claims.yaml"])
        working_clone.index.commit("add custom claim")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        claim = await db_session.get(CustomClaim, UUIDType(claim_id))
        assert claim is not None
        assert claim.name == "allowed_campus_ids"
        assert claim.organization_id == UUIDType(org_id)
        assert claim.query["select"] == "campus_id"

    async def test_pull_table_with_policies_round_trip(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull a table with non-trivial policies → DB row has policies in `access`,
        and the AST round-trips back to the YAML manifest unchanged.

        Covers Task F: prove that table policies survive a full manifest cycle
        (manifest → DB → manifest), including a role-gated policy authored
        with the canonical ``{"call": "has_role", "args": [...]}`` form. The
        manifest import path validates the AST through ``TablePolicies``
        before persisting, so non-canonical shorthand (e.g. ``{"has_role":
        "support"}``) is rejected — see the parallel
        ``test_pull_table_with_invalid_policy_when_clause_is_rejected``.
        """
        from uuid import UUID as UUIDType

        from src.models.orm.tables import Table

        work_dir = Path(working_clone.working_dir)
        table_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "tables.yaml").write_text(yaml.dump({
            "tables": {
                "with_policies": {
                    "id": table_id,
                    "description": "Table with non-trivial policies",
                    "policies": [
                        {
                            "name": "admin_bypass",
                            "actions": ["read", "create", "update", "delete"],
                            "when": {"user": "is_platform_admin"},
                        },
                        {
                            "name": "own_row",
                            "actions": ["read", "update", "delete"],
                            "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
                        },
                        {
                            "name": "support_read",
                            "actions": ["read"],
                            "when": {"call": "has_role", "args": ["support"]},
                        },
                    ],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/tables.yaml"])
        working_clone.index.commit("table with policies")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # 1) DB row carries the policies in `access` JSONB
        table = await db_session.get(Table, UUIDType(table_id))
        assert table is not None
        assert table.access is not None
        policy_names = [p["name"] for p in table.access["policies"]]
        assert "admin_bypass" in policy_names
        assert "own_row" in policy_names
        assert "support_read" in policy_names

        # AST is preserved verbatim — the row-vs-user equality check
        own = next(p for p in table.access["policies"] if p["name"] == "own_row")
        assert own["when"] == {"eq": [{"row": "created_by"}, {"user": "user_id"}]}
        assert sorted(own["actions"]) == ["delete", "read", "update"]

        # The has_role call form is what the evaluator/compiler accepts;
        # the function registry matches the literal arg against role_names
        # OR role_ids at evaluation time.
        support = next(p for p in table.access["policies"] if p["name"] == "support_read")
        assert support["when"] == {"call": "has_role", "args": ["support"]}
        assert support["actions"] == ["read"]

        # 2) Round-trip back to manifest: serialize the DB row and verify the
        # policies block survives unchanged.
        from src.services.manifest_generator import serialize_table

        round_tripped = serialize_table(table)
        assert round_tripped.policies is not None
        rt_dump = [p.model_dump(mode="json") for p in round_tripped.policies]
        rt_names = [p["name"] for p in rt_dump]
        assert rt_names == ["admin_bypass", "own_row", "support_read"]

        rt_own = next(p for p in rt_dump if p["name"] == "own_row")
        assert rt_own["when"] == {"eq": [{"row": "created_by"}, {"user": "user_id"}]}
        assert sorted(rt_own["actions"]) == ["delete", "read", "update"]

        rt_support = next(p for p in rt_dump if p["name"] == "support_read")
        assert rt_support["when"] == {"call": "has_role", "args": ["support"]}

    async def test_pull_table_with_invalid_policy_when_clause_is_rejected(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Manifest carrying an unparseable policy AST is rejected at import.

        Security boundary: ``ManifestPolicy.when`` is ``dict | None`` — the
        manifest model does NOT validate the AST. Without revalidation at the
        DB-write boundary, a malformed ``tables.yaml`` would land an
        unparseable policy in ``Table.access`` (later crashing the evaluator
        or — worse — being silently treated as deny-all). The import path
        revalidates through ``TablePolicies`` so this fails loudly with
        ``success=False``, and the table is NOT created.
        """
        from uuid import UUID as UUIDType

        from src.models.orm.tables import Table

        work_dir = Path(working_clone.working_dir)
        table_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "tables.yaml").write_text(yaml.dump({
            "tables": {
                "bad_policy": {
                    "id": table_id,
                    "description": "Has an unparseable when AST",
                    "policies": [
                        {
                            "name": "broken",
                            "actions": ["read"],
                            # INVALID_OP is not a known operator — the
                            # AST validator must reject this.
                            "when": {"INVALID_OP": []},
                        },
                    ],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/tables.yaml"])
        working_clone.index.commit("table with bad policy")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)

        # Sync as a whole must fail — no silent success that landed bad data.
        assert result.success is False, (
            f"Malformed policy must be rejected; got success result: {result}"
        )
        # Error string mentions either the unknown operator or validation.
        err = (result.error or "").lower()
        assert (
            "invalid_op" in err
            or "unknown operator" in err
            or "validation error" in err
        ), f"Error should identify the AST problem; got: {result.error!r}"

        # Side-effect check: the table was NOT created in the DB. Sync uses a
        # nested transaction around _import_all_entities (see desktop_sync
        # step 4); the ValidationError raised inside that block aborts the
        # whole import — so the bad row never lands.
        await db_session.rollback()
        table = await db_session.get(Table, UUIDType(table_id))
        assert table is None, (
            f"Bad policy must not produce a Table row; found {table!r}"
        )

    async def test_pull_event_source_from_manifest(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull manifest with schedule event source → creates EventSource + ScheduleSource + Subscription."""
        from src.models.orm.events import EventSource, EventSubscription, ScheduleSource

        work_dir = Path(working_clone.working_dir)
        es_id = str(uuid4())
        sub_id = str(uuid4())
        wf_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Need a workflow for the subscription to reference
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "sync_job.py").write_text(SAMPLE_WORKFLOW_CLEAN)

        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "sync_job": {
                    "id": wf_id,
                    "path": "workflows/sync_job.py",
                    "function_name": "clean_wf",
                },
            },
        }, default_flow_style=False))

        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "Daily Sync": {
                    "id": es_id,
                    "source_type": "schedule",
                    "cron_expression": "0 6 * * *",
                    "timezone": "America/New_York",
                    "schedule_enabled": True,
                    "subscriptions": [
                        {
                            "id": sub_id,
                            "workflow_id": wf_id,
                            "event_type": "scheduled",
                        },
                    ],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add(["workflows/sync_job.py", ".bifrost/workflows.yaml", ".bifrost/events.yaml"])
        working_clone.index.commit("add event source")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify event source in DB
        from uuid import UUID as UUIDType
        es = await db_session.get(EventSource, UUIDType(es_id))
        assert es is not None
        # source_type may be an enum or string depending on how it was inserted
        source_type = es.source_type.value if hasattr(es.source_type, "value") else es.source_type
        assert source_type == "schedule"

        # Verify schedule source
        sched_result = await db_session.execute(
            select(ScheduleSource).where(ScheduleSource.event_source_id == UUIDType(es_id))
        )
        sched = sched_result.scalar_one_or_none()
        assert sched is not None
        assert sched.cron_expression == "0 6 * * *"
        assert sched.timezone == "America/New_York"

        # Verify subscription
        sub_result = await db_session.execute(
            select(EventSubscription).where(EventSubscription.event_source_id == UUIDType(es_id))
        )
        sub = sub_result.scalar_one_or_none()
        assert sub is not None
        assert str(sub.workflow_id) == wf_id
        assert sub.event_type == "scheduled"

    async def test_pull_event_source_updates_organization_id(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull event source without org, then update manifest to add org → verify org_id set."""
        from src.models.orm.events import EventSource
        from src.models.orm.organizations import Organization

        work_dir = Path(working_clone.working_dir)
        es_id = str(uuid4())
        org_id = str(uuid4())

        # Create org in DB (needed for FK)
        from uuid import UUID as UUIDType
        org = Organization(id=UUIDType(org_id), name="EventSourceOrgTest", created_by="test")
        db_session.add(org)
        await db_session.commit()

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # First pull: event source without organization_id
        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "Global Source": {
                    "id": es_id,
                    "source_type": "webhook",
                    "adapter_name": "generic",
                    "subscriptions": [],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/events.yaml"])
        working_clone.index.commit("add global event source")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Verify no org_id
        es = await db_session.get(EventSource, UUIDType(es_id))
        assert es is not None
        assert es.organization_id is None

        # Second pull: add organization_id to the event source
        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "Global Source": {
                    "id": es_id,
                    "source_type": "webhook",
                    "adapter_name": "generic",
                    "organization_id": org_id,
                    "subscriptions": [],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/events.yaml"])
        working_clone.index.commit("assign org to event source")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Refresh and verify org_id is set
        await db_session.refresh(es)
        assert str(es.organization_id) == org_id

    async def test_pull_idempotent(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pulling the same manifest twice doesn't create duplicates."""
        from src.models.orm.integrations import Integration

        work_dir = Path(working_clone.working_dir)
        integ_id = str(uuid4())

        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "IdempotentInteg": {
                    "id": integ_id,
                    "entity_id": "tenant_id",
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("add integration")
        working_clone.remotes.origin.push()

        # Pull twice
        result1 = await sync_service.desktop_sync(confirm_deletes=True)
        assert result1.success is True
        result2 = await sync_service.desktop_sync(confirm_deletes=True)
        assert result2.success is True

        # Should still be exactly one integration with that ID
        from uuid import UUID as UUIDType
        integ_result = await db_session.execute(
            select(Integration).where(Integration.id == UUIDType(integ_id))
        )
        integs = integ_result.scalars().all()
        assert len(integs) == 1

    async def test_pull_reads_legacy_format(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pull from repo with legacy metadata.yaml still works."""
        work_dir = Path(working_clone.working_dir)

        wf_id = str(uuid4())
        wf_dir = work_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "git_sync_test_legacy.py").write_text(SAMPLE_WORKFLOW_PY)

        # Write legacy single-file manifest
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "metadata.yaml").write_text(_make_manifest(
            workflows={
                "Legacy Test Workflow": {
                    "id": wf_id,
                    "path": "workflows/git_sync_test_legacy.py",
                    "function_name": "git_sync_test_wf",
                    "type": "workflow",
                }
            }
        ))

        working_clone.index.add([
            "workflows/git_sync_test_legacy.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("add legacy format workflow")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True
        assert result.entities_imported > 0

        # Verify workflow imported
        wf_result = await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )
        wf = wf_result.scalar_one_or_none()
        assert wf is not None
        # Name comes from manifest (source of truth); indexer only sets name
        # on initial registration (when DB field is NULL), not on re-index.
        assert wf.name == "Legacy Test Workflow"

    async def test_pull_integration_preserves_config_values(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pulling integration manifest preserves existing Config values
        that reference IntegrationConfigSchema rows."""
        from uuid import UUID as UUIDType

        from src.models.orm.config import Config
        from src.models.orm.integrations import IntegrationConfigSchema

        work_dir = Path(working_clone.working_dir)
        integ_id = str(uuid4())

        # First pull: create integration with config schema
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "ConfigTestInteg": {
                    "id": integ_id,
                    "config_schema": [
                        {"key": "api_url", "type": "string", "required": True, "position": 0},
                        {"key": "api_key", "type": "secret", "required": True, "position": 1},
                    ],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("add integration")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Manually create Config values (simulating user setting values in UI)
        cs_result = await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == UUIDType(integ_id)
            )
        )
        schemas = {cs.key: cs for cs in cs_result.scalars().all()}

        config_api_url = Config(
            integration_id=UUIDType(integ_id),
            organization_id=None,
            key="api_url",
            value={"value": "https://my-instance.example.com"},
            config_type="string",
            config_schema_id=schemas["api_url"].id,
            updated_by="test",
        )
        config_api_key = Config(
            integration_id=UUIDType(integ_id),
            organization_id=None,
            key="api_key",
            value={"value": "super-secret-key-encrypted"},
            config_type="secret",
            config_schema_id=schemas["api_key"].id,
            updated_by="test",
        )
        db_session.add_all([config_api_url, config_api_key])
        await db_session.commit()

        # Second pull: same manifest — config values must survive
        (work_dir / ".bifrost" / "trigger.txt").write_text("trigger re-sync")
        working_clone.index.add([".bifrost/trigger.txt"])
        working_clone.index.commit("trigger re-sync")
        working_clone.remotes.origin.push()

        result2 = await sync_service.desktop_sync(confirm_deletes=True)
        assert result2.success is True

        # Verify Config values still exist
        db_session.expire_all()
        cfg_result = await db_session.execute(
            select(Config).where(Config.integration_id == UUIDType(integ_id))
        )
        configs = {c.key: c for c in cfg_result.scalars().all()}
        assert "api_url" in configs, "api_url Config was destroyed by sync"
        assert configs["api_url"].value == {"value": "https://my-instance.example.com"}
        assert "api_key" in configs, "api_key Config was destroyed by sync"
        assert configs["api_key"].value == {"value": "super-secret-key-encrypted"}

    async def test_pull_integration_preserves_mapping_identity(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Pulling integration manifest preserves existing mapping rows (upsert, not recreate)."""
        from uuid import UUID as UUIDType

        from src.models.orm.integrations import IntegrationMapping
        from src.models.orm.organizations import Organization

        work_dir = Path(working_clone.working_dir)
        integ_id = str(uuid4())
        org_id = str(uuid4())

        # Create org in DB (needed for FK)
        org = Organization(id=UUIDType(org_id), name="MappingTestOrg", created_by="test")
        db_session.add(org)
        await db_session.commit()

        # First pull: create integration with mapping
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "MappingTestInteg": {
                    "id": integ_id,
                    "mappings": [
                        {"organization_id": org_id, "entity_id": "tenant-1"},
                    ],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("add integration")
        working_clone.remotes.origin.push()

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Get original mapping row ID
        mapping_result = await db_session.execute(
            select(IntegrationMapping).where(
                IntegrationMapping.integration_id == UUIDType(integ_id)
            )
        )
        mapping = mapping_result.scalar_one()
        original_mapping_id = mapping.id

        # Second pull: same manifest
        (work_dir / ".bifrost" / "trigger.txt").write_text("trigger re-sync")
        working_clone.index.add([".bifrost/trigger.txt"])
        working_clone.index.commit("trigger re-sync")
        working_clone.remotes.origin.push()

        result2 = await sync_service.desktop_sync(confirm_deletes=True)
        assert result2.success is True

        # Verify mapping was preserved (not deleted + re-created)
        db_session.expire_all()
        mapping_result2 = await db_session.execute(
            select(IntegrationMapping).where(
                IntegrationMapping.integration_id == UUIDType(integ_id)
            )
        )
        mapping2 = mapping_result2.scalar_one()
        assert mapping2.entity_id == "tenant-1"
        assert mapping2.id == original_mapping_id, "Mapping was recreated instead of upserted"

    async def test_pull_integration_schema_key_add_remove(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Adding/removing config schema keys via manifest works correctly."""
        from uuid import UUID as UUIDType

        from src.models.orm.config import Config
        from src.models.orm.integrations import IntegrationConfigSchema

        work_dir = Path(working_clone.working_dir)
        integ_id = str(uuid4())
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Pull 1: Two keys
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "SchemaTestInteg": {
                    "id": integ_id,
                    "config_schema": [
                        {"key": "keep_me", "type": "string", "required": True, "position": 0},
                        {"key": "remove_me", "type": "string", "required": False, "position": 1},
                    ],
                },
            },
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("initial schema")
        working_clone.remotes.origin.push()
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True

        # Add a Config value for "keep_me"
        cs_result = await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == UUIDType(integ_id),
                IntegrationConfigSchema.key == "keep_me",
            )
        )
        keep_schema = cs_result.scalar_one()
        config_val = Config(
            integration_id=UUIDType(integ_id),
            organization_id=None,
            key="keep_me",
            value={"value": "preserved"},
            config_type="string",
            config_schema_id=keep_schema.id,
            updated_by="test",
        )
        db_session.add(config_val)
        await db_session.commit()

        # Pull 2: Remove "remove_me", add "new_key"
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "SchemaTestInteg": {
                    "id": integ_id,
                    "config_schema": [
                        {"key": "keep_me", "type": "string", "required": True, "position": 0},
                        {"key": "new_key", "type": "int", "required": False, "position": 1},
                    ],
                },
            },
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("update schema")
        working_clone.remotes.origin.push()
        result2 = await sync_service.desktop_sync(confirm_deletes=True)
        assert result2.success is True

        # Verify: keep_me config value survived, remove_me schema gone, new_key added
        cs_result2 = await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == UUIDType(integ_id)
            ).order_by(IntegrationConfigSchema.position)
        )
        schemas = cs_result2.scalars().all()
        schema_keys = [s.key for s in schemas]
        assert "keep_me" in schema_keys
        assert "new_key" in schema_keys
        assert "remove_me" not in schema_keys

        cfg_result = await db_session.execute(
            select(Config).where(
                Config.integration_id == UUIDType(integ_id),
                Config.key == "keep_me",
            )
        )
        cfg = cfg_result.scalar_one_or_none()
        assert cfg is not None, "keep_me Config value was destroyed"
        assert cfg.value == {"value": "preserved"}


# =============================================================================
# Cross-Instance Manifest Reconciliation
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestCrossInstanceManifestReconciliation:
    """Test that manifest regeneration + commit + pull correctly reconciles
    cross-instance changes to .bifrost/*.yaml files."""

    async def test_config_add_and_delete_merge(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
        tmp_path,
    ):
        """
        Instance A (working_clone) adds a config.
        Instance B (sync_service/prod) deletes a config.
        After sync, both changes should be reflected.
        """
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration

        # --- Setup: Create initial state with an integration and 2 configs ---
        integ_id = uuid4()
        config_1_id = uuid4()
        config_2_id = uuid4()

        integ = Integration(id=integ_id, name="TestReconcileInteg", is_deleted=False)
        db_session.add(integ)
        await db_session.flush()  # FK: configs reference integration_id
        cfg1 = Config(
            id=config_1_id, key="keep_this", value="yes",
            integration_id=integ_id, updated_by="git-sync",
        )
        cfg2 = Config(
            id=config_2_id, key="delete_this", value="remove_me",
            integration_id=integ_id, updated_by="git-sync",
        )
        db_session.add_all([cfg1, cfg2])

        # Also need a workflow so the manifest isn't empty
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id, name="Reconcile Test WF",
            function_name="reconcile_test_wf",
            path="workflows/git_sync_test_reconcile.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write workflow file + manifest to persistent dir
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_reconcile.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)

        # Commit + push initial state
        commit_result = await sync_service.desktop_commit("initial with configs")
        assert commit_result.success
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success

        # --- Instance A (working_clone): Pull, add config-3, push ---
        working_clone.remotes.origin.pull("main")
        clone_dir = Path(working_clone.working_dir)

        # Read current configs.yaml and add a new config
        configs_yaml_path = clone_dir / ".bifrost" / "configs.yaml"
        configs_yaml = yaml.safe_load(configs_yaml_path.read_text())
        config_3_id = str(uuid4())
        configs_yaml["configs"]["new_from_dev"] = {
            "id": config_3_id,
            "key": "new_from_dev",
            "value": "hello_from_dev",
            "integration_id": str(integ_id),
        }
        configs_yaml_path.write_text(
            yaml.dump(configs_yaml, default_flow_style=False, sort_keys=False)
        )
        working_clone.index.add([".bifrost/configs.yaml"])
        working_clone.index.commit("Dev: add new_from_dev config")
        working_clone.remotes.origin.push("main")

        # --- Instance B (prod/sync_service): Delete config-2 from DB ---
        await db_session.execute(
            delete(Config).where(Config.id == config_2_id)
        )
        await db_session.commit()

        # --- Sync: commit (regenerates manifest without config-2) then pull ---
        commit_result = await sync_service.desktop_commit("Prod: delete config-2")
        assert commit_result.success

        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # --- Verify: manifest should have config-1 and new_from_dev, NOT config-2 ---
        persistent_dir = sync_service._persistent_dir
        final_manifest = read_manifest_from_dir(persistent_dir / ".bifrost")

        config_key_names = {c.key for c in final_manifest.configs.values()}
        assert "keep_this" in config_key_names, f"config-1 should be preserved, got: {config_key_names}"
        assert "new_from_dev" in config_key_names, f"dev's new config should be merged in, got: {config_key_names}"
        assert "delete_this" not in config_key_names, f"config-2 should be deleted, got: {config_key_names}"

    async def test_empty_repo_pull_imports_remote_state(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
        tmp_path,
    ):
        """
        Instance A pushes configs/integrations to remote.
        Instance B has empty _repo (post-upgrade), pulls.
        All remote entities should be imported, not deleted.
        """
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration

        # --- Instance A: Push state with integration + config ---
        clone_dir = Path(working_clone.working_dir)
        (clone_dir / ".bifrost").mkdir(exist_ok=True)

        integ_id = str(uuid4())
        config_id = str(uuid4())

        (clone_dir / ".bifrost" / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "TestRemoteInteg": {
                    "id": integ_id,
                    "entity_id": "tenant_id",
                }
            }
        }, default_flow_style=False))

        (clone_dir / ".bifrost" / "configs.yaml").write_text(yaml.dump({
            "configs": {
                "remote_config": {
                    "id": config_id,
                    "key": "remote_config",
                    "value": "from_remote",
                    "integration_id": integ_id,
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml", ".bifrost/configs.yaml"])
        working_clone.index.commit("Remote: add integration and config")
        working_clone.remotes.origin.push("main")

        # --- Instance B: Pull from empty _repo ---
        # The sync_service starts with an empty persistent dir (no .bifrost/ files).
        # desktop_sync should regenerate (producing empty manifest), then merge remote.
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Verify the remote entities were imported into DB
        integ_result = await db_session.execute(
            select(Integration).where(Integration.name == "TestRemoteInteg")
        )
        imported_integ = integ_result.scalar_one_or_none()
        assert imported_integ is not None, "Integration should be imported from remote"

        config_result = await db_session.execute(
            select(Config).where(Config.key == "remote_config")
        )
        imported_config = config_result.scalar_one_or_none()
        assert imported_config is not None, "Config should be imported from remote"


# =============================================================================
# Pull Upsert Natural Key Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestPullUpsertNaturalKeys:
    """Test that _import_* methods handle ID mismatches by matching on natural keys."""

    async def test_workflow_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When a workflow exists with (path, function_name) but a different ID,
        the import should update the existing row's ID to match the manifest."""
        # Create a workflow in the DB with ID_A
        id_a = uuid4()
        wf = Workflow(
            id=id_a,
            name="Original",
            function_name="natural_key_test_wf",
            path="workflows/natural_key_test.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write the workflow file to the persistent dir
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/natural_key_test.py",
            SAMPLE_WORKFLOW_PY,
        )

        # Push from "another instance" (working_clone) with same (path, function_name) but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        # Write workflow file
        wf_dir = clone_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "natural_key_test.py").write_text(SAMPLE_WORKFLOW_PY)

        # Write manifest with ID_B for the same path+function_name
        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "workflows": {
                "natural_key_test_wf": {
                    "id": str(id_b),
                    "path": "workflows/natural_key_test.py",
                    "function_name": "natural_key_test_wf",
                    "type": "workflow",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            "workflows/natural_key_test.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add workflow with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — this should NOT raise IntegrityError
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Verify: only one workflow row exists with the manifest's ID (id_b)
        result = await db_session.execute(
            select(Workflow).where(
                Workflow.path == "workflows/natural_key_test.py",
                Workflow.function_name == "natural_key_test_wf",
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 workflow row, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"

    async def test_integration_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When an integration exists with same name but different ID,
        the import should update the existing row's ID to match the manifest."""
        from src.models.orm.integrations import Integration

        # Create integration in DB with ID_A
        id_a = uuid4()
        integ = Integration(id=id_a, name="NaturalKeyTestInteg", is_deleted=False)
        db_session.add(integ)
        await db_session.commit()

        # Push from "another instance" with same name but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "integrations": {
                "NaturalKeyTestInteg": {
                    "id": str(id_b),
                    "entity_id": "tenant_id",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([".bifrost/metadata.yaml"])
        working_clone.index.commit("Add integration with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Verify: one integration with manifest ID
        result = await db_session.execute(
            select(Integration).where(Integration.name == "NaturalKeyTestInteg")
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 integration, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"

    async def test_integration_import_with_different_id_and_config_schema(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """Regression for #148: integration UUID rewrite + existing config_schema rows
        must not collide with the FK ON UPDATE CASCADE-migrated rows.

        Pre-fix: prefetch cache is keyed on the OLD integration_id. After the
        upsert rewrites the integration's id, the cascade migrates the schema
        rows to the new id, but the cache lookup at the new id returns {},
        the loop falls through to INSERT, and hits a unique violation on
        (integration_id, key).
        """
        from src.models.orm.integrations import Integration, IntegrationConfigSchema

        # Existing integration with config_schema rows under id_a
        id_a = uuid4()
        integ = Integration(id=id_a, name="CascadeSchemaInteg", is_deleted=False)
        db_session.add(integ)
        await db_session.flush()
        db_session.add_all([
            IntegrationConfigSchema(
                integration_id=id_a, key="api_key", type="secret",
                required=True, position=0,
            ),
            IntegrationConfigSchema(
                integration_id=id_a, key="region", type="string",
                required=False, position=1,
            ),
        ])
        await db_session.commit()

        # Manifest references the same integration name with a different UUID,
        # plus the same two schema keys.
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)
        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "CascadeSchemaInteg": {
                    "id": str(id_b),
                    "config_schema": [
                        {"key": "api_key", "type": "secret", "required": True, "position": 0},
                        {"key": "region", "type": "string", "required": False, "position": 1},
                    ],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("integration with cross-env id + config schema")
        working_clone.remotes.origin.push("main")

        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Integration now has id_b and exactly the two schema rows under id_b.
        integ_rows = (await db_session.execute(
            select(Integration).where(Integration.name == "CascadeSchemaInteg")
        )).scalars().all()
        assert len(integ_rows) == 1
        assert integ_rows[0].id == id_b

        cs_rows = (await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == id_b
            ).order_by(IntegrationConfigSchema.position)
        )).scalars().all()
        assert [c.key for c in cs_rows] == ["api_key", "region"]
        # No orphan rows under id_a
        orphans = (await db_session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == id_a
            )
        )).scalars().all()
        assert orphans == []

    async def test_integration_import_with_different_id_and_mapping(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """Same #148 cache-staleness pattern, applied to integration_mappings:
        the unique index is (integration_id, organization_id), and the cache
        key is the OLD integration_id."""
        from src.models.orm.integrations import Integration, IntegrationMapping
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        db_session.add(Organization(id=org_id, name="MappingCascadeOrg", created_by="test"))

        id_a = uuid4()
        db_session.add(Integration(id=id_a, name="CascadeMappingInteg", is_deleted=False))
        await db_session.flush()
        db_session.add(IntegrationMapping(
            integration_id=id_a, organization_id=org_id, entity_id="tenant-a",
        ))
        await db_session.commit()

        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)
        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "CascadeMappingInteg": {
                    "id": str(id_b),
                    "mappings": [
                        {"organization_id": str(org_id), "entity_id": "tenant-a"},
                    ],
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/integrations.yaml"])
        working_clone.index.commit("integration with cross-env id + mapping")
        working_clone.remotes.origin.push("main")

        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        m_rows = (await db_session.execute(
            select(IntegrationMapping).where(
                IntegrationMapping.integration_id == id_b
            )
        )).scalars().all()
        assert len(m_rows) == 1
        assert m_rows[0].entity_id == "tenant-a"
        assert m_rows[0].organization_id == org_id

    async def test_integration_import_with_different_id_and_config(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """Same #148 cache-staleness pattern, applied to configs that
        reference the integration. The Config unique index is
        (integration_id, organization_id, key); after CASCADE the existing
        config row sits at the new integ_id, so a fresh INSERT collides."""
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration

        id_a = uuid4()
        db_session.add(Integration(id=id_a, name="CascadeConfigInteg", is_deleted=False))
        await db_session.flush()
        cfg_id = uuid4()
        db_session.add(Config(
            id=cfg_id, key="api_url", value={"value": "https://old.example.com"},
            integration_id=id_a, organization_id=None,
            updated_by="test",
        ))
        await db_session.commit()

        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)
        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "CascadeConfigInteg": {
                    "id": str(id_b),
                },
            },
        }, default_flow_style=False))
        # Manifest carries the same config under the NEW integ_id (cross-env
        # parent rewrite is the trigger for the cache-staleness bug here).
        new_cfg_id = uuid4()
        (bifrost_dir / "configs.yaml").write_text(yaml.dump({
            "configs": {
                "api_url": {
                    "id": str(new_cfg_id),
                    "key": "api_url",
                    "config_type": "string",
                    "value": {"value": "https://new.example.com"},
                    "integration_id": str(id_b),
                },
            },
        }, default_flow_style=False))

        working_clone.index.add([
            ".bifrost/integrations.yaml",
            ".bifrost/configs.yaml",
        ])
        working_clone.index.commit("integration with cross-env id + config")
        working_clone.remotes.origin.push("main")

        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        cfg_rows = (await db_session.execute(
            select(Config).where(
                Config.key == "api_url",
                Config.integration_id == id_b,
            )
        )).scalars().all()
        assert len(cfg_rows) == 1, f"Expected 1 config row under integ id_b, got {len(cfg_rows)}"
        assert cfg_rows[0].value == {"value": "https://new.example.com"}
        # And nothing left under the old integ id (CASCADE moved them all)
        orphans = (await db_session.execute(
            select(Config).where(Config.integration_id == id_a)
        )).scalars().all()
        assert orphans == []

    async def test_app_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When an app exists with same slug but different ID,
        the import should update the existing row's ID to match the manifest."""
        from src.models.orm.applications import Application

        # Create app in DB with ID_A
        id_a = uuid4()
        app = Application(
            id=id_a, name="Natural Key App", slug="natural-key-app",
            organization_id=None,
            repo_path="apps/natural-key-app",
        )
        db_session.add(app)
        await db_session.commit()

        # Push from "another instance" with same slug but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        # Write app layout file
        app_dir = clone_dir / "apps" / "natural-key-app"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "_layout.tsx").write_text("export default function Layout({ children }) { return <>{children}</>; }\n")

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "apps": {
                "natural-key-app": {
                    "id": str(id_b),
                    "path": "apps/natural-key-app",
                    "slug": "natural-key-app",
                    "name": "Natural Key App Updated",
                    "description": "Updated from remote",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            "apps/natural-key-app/_layout.tsx",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add app with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Verify: one app with manifest ID
        result = await db_session.execute(
            select(Application).where(Application.slug == "natural-key-app")
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 app, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"

    async def test_config_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When a config exists with same (integration_id, org_id, key) but different ID,
        the import should update the existing row's ID to match the manifest."""
        from src.models.orm.config import Config
        from src.models.orm.integrations import Integration

        # Create integration first (needed for FK)
        integ_id = uuid4()
        integ = Integration(id=integ_id, name="ConfigTestInteg", is_deleted=False)
        db_session.add(integ)
        await db_session.flush()

        # Create config in DB with ID_A
        id_a = uuid4()
        cfg = Config(
            id=id_a, key="natural_key_cfg", value={"test": True},
            integration_id=integ_id, updated_by="test",
        )
        db_session.add(cfg)
        await db_session.commit()

        # Push from "another instance" with same natural key but ID_B
        id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "integrations": {
                "ConfigTestInteg": {
                    "id": str(integ_id),
                    "entity_id": "tenant_id",
                }
            },
            "configs": {
                "natural_key_cfg": {
                    "id": str(id_b),
                    "key": "natural_key_cfg",
                    "integration_id": str(integ_id),
                    "config_type": "string",
                    "value": {"test": True, "updated": True},
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([".bifrost/metadata.yaml"])
        working_clone.index.commit("Add config with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Verify: one config row with manifest ID
        result = await db_session.execute(
            select(Config).where(
                Config.key == "natural_key_cfg",
                Config.integration_id == integ_id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 config, got {len(rows)}"
        assert rows[0].id == id_b, f"Expected manifest ID {id_b}, got {rows[0].id}"

    async def test_event_subscription_import_with_different_id(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """When an event subscription exists with same (event_source_id, workflow_id)
        but different ID, the import should update the existing row."""
        from src.models.orm.events import EventSource, EventSubscription

        # Create a workflow (needed for FK)
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id, name="SubTestWF", function_name="sub_test_wf",
            path="workflows/sub_test.py", is_active=True,
        )
        db_session.add(wf)

        # Create event source
        es_id = uuid4()
        es = EventSource(
            id=es_id, name="SubTestSource", source_type="schedule",
            is_active=True, created_by="test",
        )
        db_session.add(es)

        # Create subscription with ID_A
        sub_id_a = uuid4()
        sub = EventSubscription(
            id=sub_id_a, event_source_id=es_id, workflow_id=wf_id,
            is_active=True, created_by="test",
        )
        db_session.add(sub)
        await db_session.commit()

        # Push from "another instance" with same (event_source_id, workflow_id) but ID_B
        sub_id_b = uuid4()
        clone_dir = Path(working_clone.working_dir)

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Need workflow file + manifest for the pull to work
        wf_dir = clone_dir / "workflows"
        wf_dir.mkdir(exist_ok=True)
        (wf_dir / "sub_test.py").write_text(SAMPLE_WORKFLOW_PY)

        # Write persistent workflow file too
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/sub_test.py",
            SAMPLE_WORKFLOW_PY,
        )

        manifest_content = yaml.dump({
            "workflows": {
                "sub_test_wf": {
                    "id": str(wf_id),
                    "path": "workflows/sub_test.py",
                    "function_name": "sub_test_wf",
                    "type": "workflow",
                }
            },
            "events": {
                str(es_id): {
                    "id": str(es_id),
                    "source_type": "schedule",
                    "is_active": True,
                    "cron_expression": "0 * * * *",
                    "subscriptions": [
                        {
                            "id": str(sub_id_b),
                            "workflow_id": str(wf_id),
                            "is_active": True,
                        }
                    ],
                }
            },
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            "workflows/sub_test.py",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add event subscription with different ID")
        working_clone.remotes.origin.push("main")

        # Pull — should NOT raise IntegrityError
        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Verify: one subscription with manifest ID
        result = await db_session.execute(
            select(EventSubscription).where(
                EventSubscription.event_source_id == es_id,
                EventSubscription.workflow_id == wf_id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1, f"Expected 1 subscription, got {len(rows)}"
        assert rows[0].id == sub_id_b, f"Expected manifest ID {sub_id_b}, got {rows[0].id}"

    async def test_app_import_custom_repo_path(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """Importing an app at a non-apps/{slug} path should preserve the canonical repo_path."""
        clone_dir = Path(working_clone.working_dir)

        app_id = uuid4()
        # App lives at a custom path, NOT apps/{slug}
        custom_path = "custom/team/dashboard"
        app_dir = clone_dir / custom_path
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "_layout.tsx").write_text("export default function Layout({ children }) { return <>{children}</>; }\n")

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "apps": {
                "team-dashboard": {
                    "id": str(app_id),
                    "path": custom_path,
                    "slug": "team-dashboard",
                    "name": "Team Dashboard",
                    "description": "Custom path app",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            f"{custom_path}/_layout.tsx",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add app at custom path")
        working_clone.remotes.origin.push("main")

        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        # Verify: repo_path is the custom path, not apps/{slug}
        from src.models.orm.applications import Application
        result = await db_session.execute(
            select(Application).where(Application.slug == "team-dashboard")
        )
        app = result.scalar_one_or_none()
        assert app is not None, "App not imported"
        assert app.repo_path == custom_path, (
            f"Expected repo_path='{custom_path}', got '{app.repo_path}'"
        )

    async def test_app_import_underscore_in_slug(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
        working_clone,
    ):
        """Importing an app whose slug contains _ (SQL LIKE wildcard) should work correctly."""
        clone_dir = Path(working_clone.working_dir)

        app_id = uuid4()
        slug = "my_app"
        app_dir = clone_dir / "apps" / slug
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "_layout.tsx").write_text("export default function Layout({ children }) { return <>{children}</>; }\n")

        bifrost_dir = clone_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        manifest_content = yaml.dump({
            "apps": {
                slug: {
                    "id": str(app_id),
                    "path": f"apps/{slug}",
                    "slug": slug,
                    "name": "My Underscore App",
                    "description": "Has underscores in slug",
                }
            }
        }, default_flow_style=False, sort_keys=False)
        (bifrost_dir / "metadata.yaml").write_text(manifest_content)

        working_clone.index.add([
            f"apps/{slug}/_layout.tsx",
            ".bifrost/metadata.yaml",
        ])
        working_clone.index.commit("Add app with underscore slug")
        working_clone.remotes.origin.push("main")

        sync_result = await sync_service.desktop_sync(confirm_deletes=True)
        assert sync_result.success, f"Sync failed: {sync_result.error}"

        from src.models.orm.applications import Application
        result = await db_session.execute(
            select(Application).where(Application.slug == slug)
        )
        app = result.scalar_one_or_none()
        assert app is not None, "App not imported"
        assert app.repo_path == f"apps/{slug}"


# =============================================================================
# Organization & Role Import Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestOrgImport:
    """Organizations: CREATE / UPDATE / RENAME / DEACTIVATE."""

    async def test_create_org(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Org in manifest, not in DB → created."""
        from src.models.orm.organizations import Organization

        org_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": org_id, "name": "TestOrg"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/organizations.yaml"])
        working_clone.index.commit("Add org")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, f"Sync failed: {result.error}"

        org = await db_session.get(Organization, org_id)
        assert org is not None, "Org not created"
        assert org.name == "TestOrg"
        assert org.is_active is True

    async def test_update_org_by_id_rename(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Org exists by ID, manifest has new name → name updated."""
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        db_session.add(Organization(id=org_id, name="OldName", is_active=True, created_by="git-sync"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": str(org_id), "name": "NewName"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/organizations.yaml"])
        working_clone.index.commit("Rename org")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one()
        assert row.name == "NewName"

    async def test_update_org_by_name_new_id(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Org exists by name with different UUID → ID updated (cross-env)."""
        from src.models.orm.organizations import Organization

        old_id = uuid4()
        new_id = uuid4()
        db_session.add(Organization(id=old_id, name="SharedOrg", is_active=True, created_by="git-sync"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": str(new_id), "name": "SharedOrg"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/organizations.yaml"])
        working_clone.index.commit("Cross-env org")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Organization).where(Organization.id == new_id)
        )).scalar_one_or_none()
        assert row is not None, "Org should have new ID"
        assert row.name == "SharedOrg"

    async def test_org_preserves_domain(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Org exists with domain/settings, manifest only has id+name → preserved."""
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        db_session.add(Organization(
            id=org_id, name="DomainOrg", domain="example.com",
            is_active=True, is_provider=True, settings={"key": "val"},
            created_by="git-sync",
        ))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": str(org_id), "name": "DomainOrg"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/organizations.yaml"])
        working_clone.index.commit("Preserve domain")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one()
        assert row.domain == "example.com", "Domain should be preserved"
        assert row.is_provider is True, "is_provider should be preserved"
        assert row.settings == {"key": "val"}, "Settings should be preserved"

    async def test_org_idempotent(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Same manifest pulled twice → no errors, same state."""
        org_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": org_id, "name": "IdempotentOrg"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/organizations.yaml"])
        working_clone.index.commit("Idempotent org")
        working_clone.remotes.origin.push("main")

        r1 = await sync_service.desktop_sync(confirm_deletes=True)
        assert r1.success
        r2 = await sync_service.desktop_sync(confirm_deletes=True)
        assert r2.success

    async def test_deactivate_removed_org(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Org in DB (is_active=True, created_by=git-sync), not in manifest → is_active=False."""
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        keep_id = uuid4()
        db_session.add(Organization(id=org_id, name="ToDeactivate", is_active=True, created_by="git-sync"))
        db_session.add(Organization(id=keep_id, name="KeepOrg", is_active=True, created_by="git-sync"))
        await db_session.commit()

        # Manifest only has keep_id
        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": str(keep_id), "name": "KeepOrg"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/organizations.yaml"])
        working_clone.index.commit("Remove org")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        deactivated = (await db_session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one()
        assert deactivated.is_active is False

        kept = (await db_session.execute(
            select(Organization).where(Organization.id == keep_id)
        )).scalar_one()
        assert kept.is_active is True

    async def test_reactivate_org(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Org in DB (is_active=False), appears in manifest → is_active=True."""
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        db_session.add(Organization(id=org_id, name="Reactivate", is_active=False, created_by="git-sync"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": str(org_id), "name": "Reactivate"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/organizations.yaml"])
        working_clone.index.commit("Reactivate org")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Organization).where(Organization.id == org_id)
        )).scalar_one()
        assert row.is_active is True


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRoleImport:
    """Roles: CREATE / UPDATE / RENAME / DEACTIVATE."""

    async def test_create_role(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Role in manifest → created."""
        from src.models.orm.users import Role

        role_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [{"id": role_id, "name": "TestRole"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/roles.yaml"])
        working_clone.index.commit("Add role")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Role).where(Role.id == role_id)
        )).scalar_one_or_none()
        assert row is not None
        assert row.name == "TestRole"

    async def test_update_role_by_id_rename(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Role exists by ID, new name → name updated."""
        from src.models.orm.users import Role

        role_id = uuid4()
        db_session.add(Role(id=role_id, name="OldRole", created_by="git-sync"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [{"id": str(role_id), "name": "RenamedRole"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/roles.yaml"])
        working_clone.index.commit("Rename role")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Role).where(Role.id == role_id)
        )).scalar_one()
        assert row.name == "RenamedRole"

    async def test_update_role_by_name_new_id(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Role exists by name, different UUID → ID updated."""
        from src.models.orm.users import Role

        old_id = uuid4()
        new_id = uuid4()
        db_session.add(Role(id=old_id, name="SharedRole", created_by="git-sync"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [{"id": str(new_id), "name": "SharedRole"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/roles.yaml"])
        working_clone.index.commit("Cross-env role")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Role).where(Role.id == new_id)
        )).scalar_one_or_none()
        assert row is not None
        assert row.name == "SharedRole"

    async def test_role_preserves_permissions(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Role exists with permissions/description → preserved on pull."""
        from src.models.orm.users import Role

        role_id = uuid4()
        db_session.add(Role(
            id=role_id, name="PermRole", created_by="git-sync",
            description="Important role", permissions={"read": True},
        ))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [{"id": str(role_id), "name": "PermRole"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/roles.yaml"])
        working_clone.index.commit("Preserve perms")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(Role).where(Role.id == role_id)
        )).scalar_one()
        assert row.description == "Important role"
        assert row.permissions == {"read": True}

    async def test_delete_removed_role(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Role in DB not in manifest → deleted."""
        from src.models.orm.users import Role

        role_id = uuid4()
        keep_id = uuid4()
        db_session.add(Role(id=role_id, name="ToDeleteRole", created_by="git-sync"))
        db_session.add(Role(id=keep_id, name="KeepRole", created_by="git-sync"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [{"id": str(keep_id), "name": "KeepRole"}]
        }, default_flow_style=False))
        working_clone.index.add([".bifrost/roles.yaml"])
        working_clone.index.commit("Remove role")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        deleted = (await db_session.execute(
            select(Role).where(Role.id == role_id)
        )).scalar_one_or_none()
        assert deleted is None

        kept = (await db_session.execute(
            select(Role).where(Role.id == keep_id)
        )).scalar_one_or_none()
        assert kept is not None


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRoleAssignmentSync:
    """Junction tables: ADD / REMOVE / FULL REPLACE."""

    async def test_workflow_role_assignment_created(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Workflow with roles:[A,B] → workflow_roles has A,B."""
        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole

        role_a = uuid4()
        role_b = uuid4()
        db_session.add(Role(id=role_a, name="RoleA", created_by="git-sync"))
        db_session.add(Role(id=role_b, name="RoleB", created_by="git-sync"))
        await db_session.commit()

        wf_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Write workflow file
        wf_path = work_dir / "workflows" / "role_test.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        # Write manifest with roles
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [
                {"id": str(role_a), "name": "RoleA"},
                {"id": str(role_b), "name": "RoleB"},
            ]
        }, default_flow_style=False))
        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "role_test_wf": {
                    "id": wf_id,
                    "path": "workflows/role_test.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                    "roles": [str(role_a), str(role_b)],
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/role_test.py",
            ".bifrost/roles.yaml",
            ".bifrost/workflows.yaml",
        ])
        working_clone.index.commit("Add workflow with roles")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, f"Sync failed: {result.error}"

        # Check junction table
        rows = (await db_session.execute(
            select(WorkflowRole.role_id).where(WorkflowRole.workflow_id == wf_id)
        )).all()
        assigned_role_ids = {row[0] for row in rows}
        assert role_a in assigned_role_ids
        assert role_b in assigned_role_ids

    async def test_workflow_role_assignment_removed(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Existing roles A,B, manifest has only A → B removed."""
        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole

        role_a = uuid4()
        role_b = uuid4()
        wf_id = uuid4()

        db_session.add(Role(id=role_a, name="KeepRA", created_by="git-sync"))
        db_session.add(Role(id=role_b, name="RemoveRB", created_by="git-sync"))
        db_session.add(Workflow(
            id=wf_id, name="role_removal_wf", function_name="git_sync_test",
            path="workflows/role_removal.py", is_active=True,
        ))
        await db_session.flush()

        # Pre-assign both roles
        db_session.add(WorkflowRole(workflow_id=wf_id, role_id=role_a, assigned_by="test"))
        db_session.add(WorkflowRole(workflow_id=wf_id, role_id=role_b, assigned_by="test"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        wf_path = work_dir / "workflows" / "role_removal.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [
                {"id": str(role_a), "name": "KeepRA"},
                {"id": str(role_b), "name": "RemoveRB"},
            ]
        }, default_flow_style=False))
        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "role_removal_wf": {
                    "id": str(wf_id),
                    "path": "workflows/role_removal.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                    "roles": [str(role_a)],  # Only A
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/role_removal.py",
            ".bifrost/roles.yaml",
            ".bifrost/workflows.yaml",
        ])
        working_clone.index.commit("Remove role B from workflow")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        rows = (await db_session.execute(
            select(WorkflowRole.role_id).where(WorkflowRole.workflow_id == wf_id)
        )).all()
        assigned = {row[0] for row in rows}
        assert role_a in assigned
        assert role_b not in assigned

    async def test_form_role_assignment_synced(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Form with roles → form_roles synced."""
        from src.models.orm.forms import FormRole
        from src.models.orm.users import Role

        role_id = uuid4()
        db_session.add(Role(id=role_id, name="FormRole", created_by="git-sync"))
        await db_session.commit()

        wf_id = uuid4()
        form_id = str(uuid4())
        org_id = uuid4()

        # Create supporting entities
        db_session.add(Workflow(
            id=wf_id, name="form_role_wf", function_name="git_sync_test",
            path="workflows/form_role.py", is_active=True,
        ))
        from src.models.orm.organizations import Organization
        db_session.add(Organization(id=org_id, name="FormRoleOrg", is_active=True, created_by="git-sync"))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Write workflow file
        wf_path = work_dir / "workflows" / "form_role.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": str(org_id), "name": "FormRoleOrg"}]
        }, default_flow_style=False))
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [{"id": str(role_id), "name": "FormRole"}]
        }, default_flow_style=False))
        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "form_role_wf": {
                    "id": str(wf_id),
                    "path": "workflows/form_role.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "forms.yaml").write_text(yaml.dump({
            "forms": {
                "RoleForm": {
                    "id": form_id,
                    "name": "RoleForm",
                    "workflow_id": str(wf_id),
                    "organization_id": str(org_id),
                    "roles": [str(role_id)],
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/form_role.py",
            ".bifrost/organizations.yaml",
            ".bifrost/roles.yaml",
            ".bifrost/workflows.yaml",
            ".bifrost/forms.yaml",
        ])
        working_clone.index.commit("Add form with role")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, f"Sync failed: {result.error}"

        rows = (await db_session.execute(
            select(FormRole.role_id).where(FormRole.form_id == form_id)
        )).all()
        assert {row[0] for row in rows} == {role_id}


@pytest.mark.e2e
@pytest.mark.asyncio
class TestImportOrder:
    """Dependency chain correctness."""

    async def test_event_sub_workflow_ref_by_path(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Subscription workflow_id is path::func → resolves."""
        from src.models.orm.events import EventSubscription

        wf_id = str(uuid4())
        es_id = str(uuid4())
        sub_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Write workflow file so it's included in manifest
        wf_path = work_dir / "workflows" / "path_ref.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "path_ref_wf": {
                    "id": wf_id,
                    "path": "workflows/path_ref.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "PathRefSource": {
                    "id": es_id,
                    "source_type": "schedule",
                    "is_active": True,
                    "cron_expression": "0 * * * *",
                    "subscriptions": [{
                        "id": sub_id,
                        "workflow_id": "workflows/path_ref.py::git_sync_test",
                        "is_active": True,
                    }],
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/path_ref.py",
            ".bifrost/workflows.yaml",
            ".bifrost/events.yaml",
        ])
        working_clone.index.commit("Event sub with path ref")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, f"Sync failed: {result.error}"

        sub = (await db_session.execute(
            select(EventSubscription).where(EventSubscription.id == sub_id)
        )).scalar_one_or_none()
        assert sub is not None, "Subscription not created"
        assert str(sub.workflow_id) == wf_id

    async def test_event_sub_workflow_ref_by_name(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Subscription workflow_id is workflow name → resolves."""
        from src.models.orm.events import EventSubscription

        wf_id = str(uuid4())
        es_id = str(uuid4())
        sub_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Write workflow file so it's included in manifest
        wf_path = work_dir / "workflows" / "name_ref.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "name_ref_wf": {
                    "id": wf_id,
                    "path": "workflows/name_ref.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "NameRefSource": {
                    "id": es_id,
                    "source_type": "schedule",
                    "is_active": True,
                    "cron_expression": "0 * * * *",
                    "subscriptions": [{
                        "id": sub_id,
                        "workflow_id": "name_ref_wf",
                        "is_active": True,
                    }],
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/name_ref.py",
            ".bifrost/workflows.yaml",
            ".bifrost/events.yaml",
        ])
        working_clone.index.commit("Event sub with name ref")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, f"Sync failed: {result.error}"

        sub = (await db_session.execute(
            select(EventSubscription).where(EventSubscription.id == sub_id)
        )).scalar_one_or_none()
        assert sub is not None
        assert str(sub.workflow_id) == wf_id

    async def test_event_sub_workflow_ref_missing(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Subscription references nonexistent workflow → graceful skip."""
        from src.models.orm.events import EventSubscription

        es_id = str(uuid4())
        sub_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "MissingWFSource": {
                    "id": es_id,
                    "source_type": "schedule",
                    "is_active": True,
                    "cron_expression": "0 * * * *",
                    "subscriptions": [{
                        "id": sub_id,
                        "workflow_id": "nonexistent_workflow",
                        "is_active": True,
                    }],
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/events.yaml"])
        working_clone.index.commit("Event sub with missing wf")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, "Pull should succeed even with missing workflow ref"

        sub = (await db_session.execute(
            select(EventSubscription).where(EventSubscription.id == sub_id)
        )).scalar_one_or_none()
        assert sub is None, "Subscription should be skipped when workflow not found"

    async def test_full_manifest_all_entity_types(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Manifest with org, role, workflow, integration, config, app, table,
        event source, form, agent — all cross-referenced — single pull succeeds."""
        from src.models.orm.organizations import Organization
        from src.models.orm.tables import Table
        from src.models.orm.applications import Application

        org_id = str(uuid4())
        role_id = str(uuid4())
        wf_id = str(uuid4())
        integ_id = str(uuid4())
        config_id = str(uuid4())
        app_id = str(uuid4())
        table_id = str(uuid4())
        es_id = str(uuid4())
        sub_id = str(uuid4())
        form_id = str(uuid4())
        agent_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        # Write workflow file
        wf_path = work_dir / "workflows" / "full_test.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        # Write app layout file
        app_dir = work_dir / "apps" / "fullapp"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "_layout.tsx").write_text("export default function Layout({ children }) { return <>{children}</>; }\n")

        # Write all manifest files
        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": org_id, "name": "FullOrg"}]
        }, default_flow_style=False))
        (bifrost_dir / "roles.yaml").write_text(yaml.dump({
            "roles": [{"id": role_id, "name": "FullRole"}]
        }, default_flow_style=False))
        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "full_test_wf": {
                    "id": wf_id,
                    "path": "workflows/full_test.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                    "organization_id": org_id,
                    "roles": [role_id],
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "integrations.yaml").write_text(yaml.dump({
            "integrations": {
                "TestFullInteg": {
                    "id": integ_id,
                    "config_schema": [],
                    "mappings": [],
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "configs.yaml").write_text(yaml.dump({
            "configs": {
                "full_cfg": {
                    "id": config_id,
                    "integration_id": integ_id,
                    "key": "full_test_key",
                    "config_type": "string",
                    "organization_id": org_id,
                    "value": "test_value",
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "apps.yaml").write_text(yaml.dump({
            "apps": {
                "fullapp": {
                    "id": app_id,
                    "path": "apps/fullapp",
                    "slug": "fullapp",
                    "name": "FullTestApp",
                    "organization_id": org_id,
                    "roles": [role_id],
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "tables.yaml").write_text(yaml.dump({
            "tables": {
                "FullTable": {
                    "id": table_id,
                    "organization_id": org_id,
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "FullEventSource": {
                    "id": es_id,
                    "source_type": "schedule",
                    "is_active": True,
                    "cron_expression": "0 * * * *",
                    "organization_id": org_id,
                    "subscriptions": [{
                        "id": sub_id,
                        "workflow_id": wf_id,
                        "is_active": True,
                    }],
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "forms.yaml").write_text(yaml.dump({
            "forms": {
                "FullTestForm": {
                    "id": form_id,
                    "name": "FullTestForm",
                    "workflow_id": wf_id,
                    "organization_id": org_id,
                    "roles": [role_id],
                }
            }
        }, default_flow_style=False))
        (bifrost_dir / "agents.yaml").write_text(yaml.dump({
            "agents": {
                "FullTestAgent": {
                    "id": agent_id,
                    "name": "FullTestAgent",
                    "system_prompt": "test",
                    "tool_ids": [wf_id],
                    "organization_id": org_id,
                    "roles": [role_id],
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/full_test.py",
            "apps/fullapp/_layout.tsx",
            ".bifrost/organizations.yaml",
            ".bifrost/roles.yaml",
            ".bifrost/workflows.yaml",
            ".bifrost/integrations.yaml",
            ".bifrost/configs.yaml",
            ".bifrost/apps.yaml",
            ".bifrost/tables.yaml",
            ".bifrost/events.yaml",
            ".bifrost/forms.yaml",
            ".bifrost/agents.yaml",
        ])
        working_clone.index.commit("Full manifest")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, f"Sync failed: {result.error}"

        # Verify key entities exist
        org = await db_session.get(Organization, org_id)
        assert org is not None, "Org not created"

        app = (await db_session.execute(
            select(Application).where(Application.id == app_id)
        )).scalar_one_or_none()
        assert app is not None, "App not created"

        table = (await db_session.execute(
            select(Table).where(Table.id == table_id)
        )).scalar_one_or_none()
        assert table is not None, "Table not created"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestEventSourceNameFix:
    """Event source name = manifest dict key, not UUID."""

    async def test_event_source_name_from_manifest_key(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """EventSource name = manifest dict key, not UUID."""
        from src.models.orm.events import EventSource

        es_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        (bifrost_dir / "events.yaml").write_text(yaml.dump({
            "events": {
                "My Cron Source": {
                    "id": es_id,
                    "source_type": "schedule",
                    "is_active": True,
                    "cron_expression": "0 * * * *",
                    "subscriptions": [],
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([".bifrost/events.yaml"])
        working_clone.index.commit("Event source name test")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        row = (await db_session.execute(
            select(EventSource).where(EventSource.id == es_id)
        )).scalar_one_or_none()
        assert row is not None
        assert row.name == "My Cron Source", f"Expected 'My Cron Source', got '{row.name}'"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestAccessLevelSync:
    """Access level synced from manifest on import."""

    async def test_workflow_access_level_synced(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Workflow with access_level in manifest → DB updated."""
        wf_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        wf_path = work_dir / "workflows" / "access_test.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "access_test_wf": {
                    "id": wf_id,
                    "path": "workflows/access_test.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                    "access_level": "authenticated",
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/access_test.py",
            ".bifrost/workflows.yaml",
        ])
        working_clone.index.commit("Workflow access level")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        wf = (await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )).scalar_one()
        assert wf.access_level == "authenticated"

    async def test_app_access_level_synced(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """App with access_level in manifest → DB updated."""
        from src.models.orm.applications import Application

        app_id = str(uuid4())
        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        app_dir = work_dir / "apps" / "accessapp"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "_layout.tsx").write_text("export default function Layout({ children }) { return <>{children}</>; }\n")

        (bifrost_dir / "apps.yaml").write_text(yaml.dump({
            "apps": {
                "accessapp": {
                    "id": app_id,
                    "path": "apps/accessapp",
                    "slug": "accessapp",
                    "name": "AccessApp",
                    "access_level": "public",
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "apps/accessapp/_layout.tsx",
            ".bifrost/apps.yaml",
        ])
        working_clone.index.commit("App access level")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        app = (await db_session.execute(
            select(Application).where(Application.id == app_id)
        )).scalar_one()
        assert app.access_level == "public"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestOrgScopedEntities:
    """organization_id FK resolution across entity types."""

    async def test_workflow_with_org_id(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Workflow references org from manifest → FK satisfied."""
        org_id = str(uuid4())
        wf_id = str(uuid4())

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        wf_path = work_dir / "workflows" / "org_test.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": org_id, "name": "WFOrg"}]
        }, default_flow_style=False))
        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "org_test_wf": {
                    "id": wf_id,
                    "path": "workflows/org_test.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                    "organization_id": org_id,
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/org_test.py",
            ".bifrost/organizations.yaml",
            ".bifrost/workflows.yaml",
        ])
        working_clone.index.commit("Workflow with org")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success, f"Sync failed: {result.error}"

        wf = (await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )).scalar_one()
        assert str(wf.organization_id) == org_id

    async def test_workflow_org_id_updated_on_pull(
        self, db_session: AsyncSession, sync_service, working_clone,
    ):
        """Existing workflow with no org, manifest adds org → org_id updated."""
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        wf_id = uuid4()

        db_session.add(Organization(id=org_id, name="AddOrgLater", is_active=True, created_by="git-sync"))
        db_session.add(Workflow(
            id=wf_id, name="no_org_wf", function_name="git_sync_test",
            path="workflows/no_org.py", is_active=True, organization_id=None,
        ))
        await db_session.commit()

        work_dir = Path(working_clone.working_dir)
        bifrost_dir = work_dir / ".bifrost"
        bifrost_dir.mkdir(exist_ok=True)

        wf_path = work_dir / "workflows" / "no_org.py"
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(SAMPLE_WORKFLOW_PY)

        (bifrost_dir / "organizations.yaml").write_text(yaml.dump({
            "organizations": [{"id": str(org_id), "name": "AddOrgLater"}]
        }, default_flow_style=False))
        (bifrost_dir / "workflows.yaml").write_text(yaml.dump({
            "workflows": {
                "no_org_wf": {
                    "id": str(wf_id),
                    "path": "workflows/no_org.py",
                    "function_name": "git_sync_test",
                    "type": "workflow",
                    "organization_id": str(org_id),
                }
            }
        }, default_flow_style=False))

        working_clone.index.add([
            "workflows/no_org.py",
            ".bifrost/organizations.yaml",
            ".bifrost/workflows.yaml",
        ])
        working_clone.index.commit("Add org to workflow")
        working_clone.remotes.origin.push("main")

        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success

        wf = (await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )).scalar_one()
        assert wf.organization_id == org_id, "org_id should be updated on existing workflow"


# =============================================================================
# Desktop Sync (combined pull + push)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestDesktopSync:
    """Combined sync operation: pull + push in a single call."""

    async def test_sync_pull_and_push_combined(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
        bare_repo,
        tmp_path,
    ):
        """Commit locally + commit in repo -> sync -> both directions work."""
        # Setup: create initial state and push to repo
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Sync Combined Test",
            function_name="sync_combined_wf",
            path="workflows/git_sync_test_combined.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_combined.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Make a change in the remote repo via working_clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        extra_file = work_dir / "workflows" / "git_sync_test_remote_extra.py"
        extra_file.parent.mkdir(parents=True, exist_ok=True)
        extra_file.write_text(SAMPLE_WORKFLOW_CLEAN)
        working_clone.index.add(["workflows/git_sync_test_remote_extra.py"])
        working_clone.index.commit("remote: add extra workflow")
        working_clone.remotes.origin.push()

        # Make a local change (modify the workflow file)
        wf.name = "Sync Combined Test Updated"
        await db_session.commit()
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_combined.py",
            SAMPLE_WORKFLOW_UPDATED,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("local: update workflow")

        # Sync should pull remote changes and push local changes
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True
        assert result.pushed_commits > 0, "Should have pushed local changes"

        # Verify: the remote extra file should now be in persistent dir
        assert (sync_service._persistent_dir / "workflows" / "git_sync_test_remote_extra.py").exists()

        # Verify: our local change should be in the remote repo
        verify_path = tmp_path / "verify_combined"
        Repo.clone_from(f"file://{bare_repo}", str(verify_path), branch="main")
        combined_file = verify_path / "workflows" / "git_sync_test_combined.py"
        assert combined_file.exists()
        assert "Updated" in combined_file.read_text()

    async def test_sync_with_conflicts_returns_sync_result(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Create divergent history -> sync -> SyncResult has conflicts, pull_success=False."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Sync Conflict Test",
            function_name="sync_conflict_wf",
            path="workflows/git_sync_test_sync_conflict.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_sync_conflict.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Modify in repo via working clone
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_sync_conflict.py"
        wf_file.write_text(SAMPLE_WORKFLOW_UPDATED)
        working_clone.index.add(["workflows/git_sync_test_sync_conflict.py"])
        working_clone.index.commit("remote: modify workflow")
        working_clone.remotes.origin.push()

        # Modify locally (different content)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_sync_conflict.py",
            SAMPLE_WORKFLOW_CLEAN,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("local: different modification")

        # Sync should detect conflict
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is False
        assert result.pull_success is False
        assert len(result.conflicts) > 0

    async def test_sync_after_resolve_pushes_merge_commit(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
        bare_repo,
        tmp_path,
    ):
        """Create conflict -> sync -> resolve -> sync again -> success, pushed_commits > 0."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Resolve Push Test",
            function_name="resolve_push_wf",
            path="workflows/git_sync_test_resolve_push.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_resolve_push.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Create conflict: modify both sides
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_resolve_push.py"
        wf_file.write_text(SAMPLE_WORKFLOW_UPDATED)
        working_clone.index.add(["workflows/git_sync_test_resolve_push.py"])
        working_clone.index.commit("remote: modify")
        working_clone.remotes.origin.push()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_resolve_push.py",
            SAMPLE_WORKFLOW_CLEAN,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("local: different change")

        # First sync: should fail with conflicts
        result1 = await sync_service.desktop_sync(confirm_deletes=True)
        assert result1.success is False
        assert len(result1.conflicts) > 0

        # Resolve conflicts by choosing "ours" for all files
        resolutions = {c.path: "ours" for c in result1.conflicts}
        resolve_result = await sync_service.desktop_resolve(resolutions)
        assert resolve_result.success is True

        # Second sync: should succeed and push the merge commit
        result2 = await sync_service.desktop_sync(confirm_deletes=True)
        assert result2.success is True
        assert result2.pushed_commits > 0


# =============================================================================
# Abort Merge
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestAbortMerge:
    """Abort an in-progress merge."""

    async def test_abort_merge_during_conflict(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Create conflict -> sync -> abort_merge -> success. Status shows clean state."""
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Abort Merge Test",
            function_name="abort_merge_wf",
            path="workflows/git_sync_test_abort_merge.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_abort_merge.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Create conflict
        work_dir = Path(working_clone.working_dir)
        working_clone.remotes.origin.pull()
        wf_file = work_dir / "workflows" / "git_sync_test_abort_merge.py"
        wf_file.write_text(SAMPLE_WORKFLOW_UPDATED)
        working_clone.index.add(["workflows/git_sync_test_abort_merge.py"])
        working_clone.index.commit("remote: modify")
        working_clone.remotes.origin.push()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_abort_merge.py",
            SAMPLE_WORKFLOW_CLEAN,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("local: different change")

        # Sync should fail with conflicts
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is False
        assert len(result.conflicts) > 0

        # Abort the merge
        abort_result = await sync_service.desktop_abort_merge()
        assert abort_result.success is True

        # Status should show clean state (no unresolved conflicts)
        status = await sync_service.desktop_status()
        assert len(status.conflicts) == 0

    async def test_abort_merge_with_no_merge_in_progress(
        self,
        db_session: AsyncSession,
        sync_service,
        working_clone,
    ):
        """Call abort_merge when there's no merge -> returns success=False, error message."""
        # Setup: just have a clean repo with no merge in progress
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="No Merge Test",
            function_name="no_merge_wf",
            path="workflows/git_sync_test_no_merge.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/git_sync_test_no_merge.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("initial commit")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Try to abort when there's no merge
        abort_result = await sync_service.desktop_abort_merge()
        assert abort_result.success is False
        assert abort_result.error is not None


# =============================================================================
# Delete Confirmation Gate
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestDeleteConfirmation:
    """Verify confirm_deletes gate blocks or allows entity deletion."""

    async def test_sync_blocks_when_stale_entities_detected(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
    ):
        """
        When DB has entities not in the manifest and confirm_deletes is False,
        sync returns success=True with needs_delete_confirmation=True.
        """
        # Create a workflow in DB
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Stale Entity Test",
            function_name="stale_entity_wf",
            path="workflows/stale_entity_wf.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write the file and manifest (includes this workflow)
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/stale_entity_wf.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("add stale entity test wf")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Now remove the file from repo but keep it in DB → makes it "stale"
        remove_entity_from_repo(sync_service._persistent_dir, "workflows/stale_entity_wf.py")
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("remove stale wf from manifest")

        # Sync WITHOUT confirm_deletes → should be blocked
        result = await sync_service.desktop_sync()
        assert result.success is True
        assert result.needs_delete_confirmation is True
        assert len(result.pending_deletes) > 0

        # The workflow should still exist in DB (not deleted)
        row = await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )
        assert row.scalar_one_or_none() is not None

    async def test_sync_deletes_when_confirmed(
        self,
        db_session: AsyncSession,
        sync_service,
        bare_repo,
    ):
        """
        When confirm_deletes=True, stale entities are deleted and
        needs_delete_confirmation is False.
        """
        # Create a workflow in DB
        wf_id = uuid4()
        wf = Workflow(
            id=wf_id,
            name="Confirm Delete Test",
            function_name="confirm_delete_wf",
            path="workflows/confirm_delete_wf.py",
            is_active=True,
        )
        db_session.add(wf)
        await db_session.commit()

        # Write file and manifest
        write_entity_to_repo(
            sync_service._persistent_dir,
            "workflows/confirm_delete_wf.py",
            SAMPLE_WORKFLOW_PY,
        )
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("add confirm delete test wf")
        await sync_service.desktop_sync(confirm_deletes=True)

        # Remove file → stale
        remove_entity_from_repo(sync_service._persistent_dir, "workflows/confirm_delete_wf.py")
        await write_manifest_to_repo(db_session, sync_service._persistent_dir)
        await sync_service.desktop_commit("remove confirm delete wf")

        # Sync WITH confirm_deletes → should proceed and delete
        result = await sync_service.desktop_sync(confirm_deletes=True)
        assert result.success is True
        assert result.needs_delete_confirmation is False

        # The workflow should be deactivated
        db_session.expire_all()
        row = await db_session.execute(
            select(Workflow).where(Workflow.id == wf_id)
        )
        deleted_wf = row.scalar_one_or_none()
        # Either deleted or deactivated
        assert deleted_wf is None or deleted_wf.is_active is False
