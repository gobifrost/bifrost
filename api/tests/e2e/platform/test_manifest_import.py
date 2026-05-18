"""
Manifest Import Tests — cross-environment rebinding via target_organization_id and role_resolution.

Covers Task 13 from docs/plans/2026-04-18-cli-mutation-surface-and-mcp-parity.md:
- `target_organization_id` rewrites org on every entity in the bundle before upsert.
- `role_resolution="name"` resolves role_names to UUIDs in the target DB.
- Missing role names fail loud with no partial writes (transaction rolls back).
- Bundle with orgs + target_organization_id is rejected.
- Idempotency: re-importing the same bundle into the same target is a no-op.

Tests write `.bifrost/*.yaml` directly to the S3 _repo/ via RepoStorage, then
call `import_manifest_from_repo` — skipping git to exercise the pure import path.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.orm.applications import Application
from src.models.orm.forms import Form, FormRole
from src.models.orm.organizations import Organization
from src.models.orm.users import Role
from src.models.orm.workflows import Workflow
from src.services.manifest_import import import_manifest_from_repo
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)

SAMPLE_WORKFLOW_PY = b'''\
from bifrost import workflow

@workflow(name="Manifest Import Test Workflow")
def manifest_import_test_wf(message: str):
    """A workflow for manifest-import tests."""
    return {"result": message}
'''


# =============================================================================
# Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def repo_storage() -> RepoStorage:
    """Real RepoStorage backed by test S3-compatible storage."""
    settings = get_settings()
    return RepoStorage(settings)


@pytest_asyncio.fixture
async def cleanup_manifest_import(db_session: AsyncSession, repo_storage: RepoStorage):
    """Wipe .bifrost/, test workflows, test orgs, test roles, test forms/apps between tests."""
    _MANIFEST_PATHS = (
        ".bifrost/workflows.yaml",
        ".bifrost/forms.yaml",
        ".bifrost/apps.yaml",
        ".bifrost/organizations.yaml",
        ".bifrost/roles.yaml",
        ".bifrost/integrations.yaml",
        ".bifrost/configs.yaml",
        ".bifrost/tables.yaml",
        ".bifrost/events.yaml",
        ".bifrost/agents.yaml",
        "workflows/manifest_import_test_wf.py",
    )

    async def _wipe_s3() -> None:
        for path in _MANIFEST_PATHS:
            try:
                await repo_storage.delete(path)
            except Exception as e:
                # Best-effort per-path cleanup; missing keys are normal
                logger.debug(f"_wipe_s3 could not delete {path}: {e}")

    # Pre-test: clear any S3 state left by prior tests so this test starts clean
    await _wipe_s3()

    yield

    # Child junction tables first
    await db_session.execute(
        delete(FormRole).where(FormRole.assigned_by == "git-sync")
    )
    # Forms created by this test
    await db_session.execute(
        delete(Form).where(Form.created_by == "git-sync")
    )
    # Apps referencing these orgs (by slug pattern)
    await db_session.execute(
        delete(Application).where(Application.slug.like("mi-test-%"))
    )
    # Workflows created by this test
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("workflows/manifest_import_test%"))
    )
    # Roles and orgs
    await db_session.execute(
        delete(Role).where(Role.name.like("mi-role-%"))
    )
    await db_session.execute(
        delete(Organization).where(Organization.name.like("mi-org-%"))
    )
    await db_session.commit()

    # Post-test: wipe S3 manifest + workflow file
    await _wipe_s3()


# =============================================================================
# Helpers
# =============================================================================


async def _write_manifest_files(repo: RepoStorage, files: dict[str, str]) -> None:
    """Write ``{filename: yaml_content}`` into ``_repo/.bifrost/``."""
    for filename, content in files.items():
        await repo.write(f".bifrost/{filename}", content.encode("utf-8"))


def _yaml(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=True)


async def _make_target_org(db: AsyncSession, name: str) -> UUID:
    org_id = uuid4()
    db.add(Organization(
        id=org_id,
        name=name,
        is_active=True,
        created_by="test",
    ))
    await db.commit()
    return org_id


async def _make_role(db: AsyncSession, name: str) -> UUID:
    role_id = uuid4()
    db.add(Role(
        id=role_id,
        name=name,
        created_by="git-sync",
    ))
    await db.commit()
    return role_id


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestTargetOrganizationIdOverride:
    """Bundle with explicit org A, target org B — entities land under B."""

    async def test_workflow_org_rewritten_to_target(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup_manifest_import,
    ):
        # Source bundle was produced for org A (never imported into target).
        source_org_id = str(uuid4())
        wf_id = str(uuid4())

        # Target environment has org B
        target_org_id = await _make_target_org(db_session, "mi-org-target")

        # Upload the workflow file
        await repo_storage.write(
            "workflows/manifest_import_test_wf.py", SAMPLE_WORKFLOW_PY,
        )

        # Manifest bundle carries org A references on the workflow, but NO
        # `organizations` section (required when using target_organization_id).
        await _write_manifest_files(repo_storage, {
            "workflows.yaml": _yaml({
                "workflows": {
                    wf_id: {
                        "id": wf_id,
                        "name": "Manifest Import Test Workflow",
                        "path": "workflows/manifest_import_test_wf.py",
                        "function_name": "manifest_import_test_wf",
                        "type": "workflow",
                        "organization_id": source_org_id,
                    }
                }
            }),
        })

        result = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=False,
            target_organization_id=target_org_id,
        )
        await db_session.commit()
        assert result.applied is True

        # Workflow landed under target org, not source.
        wf_row = (await db_session.execute(
            select(Workflow).where(Workflow.id == UUID(wf_id))
        )).scalar_one()
        assert wf_row.organization_id == target_org_id


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRoleResolutionByName:
    """Bundle with role_names, target env with matching roles → resolved."""

    async def test_form_role_names_resolved(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup_manifest_import,
    ):
        target_org_id = await _make_target_org(db_session, "mi-org-roletarget")
        admin_role_id = await _make_role(db_session, "mi-role-admin")

        form_id = str(uuid4())
        wf_id = str(uuid4())

        # Upload workflow file the form points at.
        await repo_storage.write(
            "workflows/manifest_import_test_wf.py", SAMPLE_WORKFLOW_PY,
        )

        await _write_manifest_files(repo_storage, {
            "workflows.yaml": _yaml({
                "workflows": {
                    wf_id: {
                        "id": wf_id,
                        "name": "Manifest Import Test Workflow",
                        "path": "workflows/manifest_import_test_wf.py",
                        "function_name": "manifest_import_test_wf",
                        "type": "workflow",
                    }
                }
            }),
            "forms.yaml": _yaml({
                "forms": {
                    form_id: {
                        "id": form_id,
                        "name": "Imported Form",
                        "role_names": ["mi-role-admin"],
                        "workflow_id": wf_id,
                        "form_schema": {"fields": []},
                    }
                }
            }),
        })

        result = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=False,
            target_organization_id=target_org_id,
            role_resolution="name",
        )
        await db_session.commit()
        assert result.applied is True, f"warnings={result.warnings}"

        # Form exists under target org with the resolved role assignment.
        form_row = (await db_session.execute(
            select(Form).where(Form.id == UUID(form_id))
        )).scalar_one()
        assert form_row.organization_id == target_org_id

        role_rows = (await db_session.execute(
            select(FormRole.role_id).where(FormRole.form_id == UUID(form_id))
        )).all()
        assert {row[0] for row in role_rows} == {admin_role_id}


@pytest.mark.e2e
@pytest.mark.asyncio
class TestRoleResolutionMissingName:
    """Bundle with unknown role name → ValueError, no partial writes."""

    async def test_unknown_role_name_aborts_entire_import(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup_manifest_import,
    ):
        target_org_id = await _make_target_org(db_session, "mi-org-strict")
        wf_id = str(uuid4())
        form_id = str(uuid4())

        # Workflow file uploaded — if resolver ran partially, this workflow
        # would already be in the DB. We assert it isn't after the failure.
        await repo_storage.write(
            "workflows/manifest_import_test_wf.py", SAMPLE_WORKFLOW_PY,
        )

        await _write_manifest_files(repo_storage, {
            "workflows.yaml": _yaml({
                "workflows": {
                    wf_id: {
                        "id": wf_id,
                        "name": "Manifest Import Test Workflow",
                        "path": "workflows/manifest_import_test_wf.py",
                        "function_name": "manifest_import_test_wf",
                        "type": "workflow",
                    }
                }
            }),
            "forms.yaml": _yaml({
                "forms": {
                    form_id: {
                        "id": form_id,
                        "name": "Imported Form",
                        "role_names": ["mi-role-does-not-exist"],
                        "workflow_id": wf_id,
                        "form_schema": {"fields": []},
                    }
                }
            }),
        })

        with pytest.raises(ValueError, match="unknown role"):
            await import_manifest_from_repo(
                db_session,
                delete_removed_entities=False,
                dry_run=False,
                target_organization_id=target_org_id,
                role_resolution="name",
            )

        # No partial writes: workflow should NOT be present in DB.
        wf_count = (await db_session.execute(
            select(Workflow).where(Workflow.id == UUID(wf_id))
        )).scalar_one_or_none()
        assert wf_count is None, "no entity should be written when role resolution fails"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestOrgsPlusTargetRejected:
    """Bundle carrying organizations + target_organization_id → rejected."""

    async def test_orgs_section_with_target_id_raises(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup_manifest_import,
    ):
        target_org_id = await _make_target_org(db_session, "mi-org-dupecheck")
        source_org_id = str(uuid4())

        await _write_manifest_files(repo_storage, {
            "organizations.yaml": _yaml({
                "organizations": [
                    {"id": source_org_id, "name": "mi-org-source", "is_active": True},
                ],
            }),
        })

        with pytest.raises(ValueError, match="cannot carry organizations section"):
            await import_manifest_from_repo(
                db_session,
                delete_removed_entities=False,
                dry_run=False,
                target_organization_id=target_org_id,
            )


@pytest.mark.e2e
@pytest.mark.asyncio
class TestIdempotency:
    """Importing the same bundle twice into the same target is a no-op."""

    async def test_second_import_produces_no_changes(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup_manifest_import,
    ):
        target_org_id = await _make_target_org(db_session, "mi-org-idempotent")
        wf_id = str(uuid4())

        await repo_storage.write(
            "workflows/manifest_import_test_wf.py", SAMPLE_WORKFLOW_PY,
        )
        # Include description to match what the WorkflowIndexer will populate
        # from the docstring on first import — otherwise the second diff sees
        # the DB's description field as a change.
        await _write_manifest_files(repo_storage, {
            "workflows.yaml": _yaml({
                "workflows": {
                    wf_id: {
                        "id": wf_id,
                        "name": "Manifest Import Test Workflow",
                        "path": "workflows/manifest_import_test_wf.py",
                        "function_name": "manifest_import_test_wf",
                        "type": "workflow",
                        "description": "A workflow for manifest-import tests.",
                    }
                }
            }),
        })

        first = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=False,
            target_organization_id=target_org_id,
        )
        await db_session.commit()
        assert first.applied is True

        # Second import — same bundle, same target.
        second = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=False,
            target_organization_id=target_org_id,
        )
        await db_session.commit()
        assert second.applied is True
        assert second.entity_changes == [], f"expected no-op, got {second.entity_changes}"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestEntityIdCherryPick:
    """entity_ids filter: apply only selected entities from a multi-entity diff.

    Exercises the TUI cherry-pick path — caller runs dry_run to see a diff,
    picks a subset, then re-submits with entity_ids to apply only the
    selection. Entities outside the set must not land in the DB.
    """

    async def test_subset_filter_writes_only_selected_entities(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup_manifest_import,
    ):
        target_org_id = await _make_target_org(db_session, "mi-org-cherrypick")
        wf_a_id = str(uuid4())
        wf_b_id = str(uuid4())

        await repo_storage.write(
            "workflows/manifest_import_test_wf.py", SAMPLE_WORKFLOW_PY,
        )
        await _write_manifest_files(repo_storage, {
            "workflows.yaml": _yaml({
                "workflows": {
                    wf_a_id: {
                        "id": wf_a_id,
                        "name": "Manifest Import Test Workflow A",
                        "path": "workflows/manifest_import_test_wf.py",
                        "function_name": "manifest_import_test_wf",
                        "type": "workflow",
                    },
                    wf_b_id: {
                        "id": wf_b_id,
                        "name": "Manifest Import Test Workflow B",
                        "path": "workflows/manifest_import_test_wf.py",
                        "function_name": "manifest_import_test_wf",
                        "type": "workflow",
                    },
                }
            }),
        })

        # Dry-run returns both workflows in the diff with their ids.
        dry = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=True,
            target_organization_id=target_org_id,
        )
        diff_ids = {c.get("id") for c in dry.entity_changes}
        assert {wf_a_id, wf_b_id}.issubset(diff_ids), f"expected both ids in diff, got {diff_ids}"

        # Apply only workflow A.
        result = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=False,
            target_organization_id=target_org_id,
            entity_ids={wf_a_id},
        )
        await db_session.commit()
        assert result.applied is True
        # entity_changes reflects what was applied, not the full diff.
        applied_ids = {c.get("id") for c in result.entity_changes}
        assert applied_ids == {wf_a_id}, f"expected only A, got {applied_ids}"

        # Workflow A landed; workflow B did not.
        a_row = (await db_session.execute(
            select(Workflow).where(Workflow.id == UUID(wf_a_id))
        )).scalar_one_or_none()
        b_row = (await db_session.execute(
            select(Workflow).where(Workflow.id == UUID(wf_b_id))
        )).scalar_one_or_none()
        assert a_row is not None, "workflow A should be written"
        assert b_row is None, "workflow B should NOT be written"

    async def test_empty_entity_ids_is_noop(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
        cleanup_manifest_import,
    ):
        """entity_ids=set() → no writes even if the bundle has changes."""
        target_org_id = await _make_target_org(db_session, "mi-org-emptyset")
        wf_id = str(uuid4())

        await repo_storage.write(
            "workflows/manifest_import_test_wf.py", SAMPLE_WORKFLOW_PY,
        )
        await _write_manifest_files(repo_storage, {
            "workflows.yaml": _yaml({
                "workflows": {
                    wf_id: {
                        "id": wf_id,
                        "name": "Manifest Import Test Workflow",
                        "path": "workflows/manifest_import_test_wf.py",
                        "function_name": "manifest_import_test_wf",
                        "type": "workflow",
                    },
                }
            }),
        })

        result = await import_manifest_from_repo(
            db_session,
            delete_removed_entities=False,
            dry_run=False,
            target_organization_id=target_org_id,
            entity_ids=set(),
        )
        await db_session.commit()
        assert result.applied is True
        assert result.entity_changes == [], f"expected no-op, got {result.entity_changes}"

        # Nothing written.
        row = (await db_session.execute(
            select(Workflow).where(Workflow.id == UUID(wf_id))
        )).scalar_one_or_none()
        assert row is None, "no workflow should be written when entity_ids is empty"
