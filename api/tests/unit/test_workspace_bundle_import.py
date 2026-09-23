"""Partial workspace imports select explicitly and never run stale deletion."""
from __future__ import annotations

from uuid import UUID
from unittest.mock import AsyncMock, call

import pytest


def test_partial_selection_rewrites_kept_conflict_references() -> None:
    from bifrost.manifest import Manifest, ManifestForm, ManifestWorkflow
    from src.services.manifest_import import PartialImportSelection, rewrite_manifest_references

    source_workflow = UUID("11111111-1111-1111-1111-111111111111")
    target_workflow = UUID("22222222-2222-2222-2222-222222222222")
    form = ManifestForm(id="33333333-3333-3333-3333-333333333333", name="Intake", workflow_id=str(source_workflow))
    rewritten = rewrite_manifest_references(
        Manifest(
            workflows={str(source_workflow): ManifestWorkflow(
                id=str(source_workflow), name="run", path="workflows/run.py", function_name="run",
            )},
            forms={form.id: form},
        ),
        PartialImportSelection(
            included_source_ids=frozenset({form.id}),
            target_ids={source_workflow: target_workflow},
        ),
    )

    assert rewritten.forms[form.id].workflow_id == str(target_workflow)
    assert rewritten.workflows[str(target_workflow)].organization_id is None


def test_partial_selection_keeps_only_explicit_source_ids() -> None:
    from bifrost.manifest import Manifest, ManifestWorkflow
    from src.services.manifest_import import PartialImportSelection, filter_partial_manifest

    first = ManifestWorkflow(id="11111111-1111-1111-1111-111111111111", name="one", path="workflows/one.py", function_name="one")
    second = ManifestWorkflow(id="22222222-2222-2222-2222-222222222222", name="two", path="workflows/two.py", function_name="two")
    selected = filter_partial_manifest(
        Manifest(workflows={first.id: first, second.id: second}),
        PartialImportSelection(included_source_ids=frozenset({first.id}), target_ids={}),
    )

    assert list(selected.workflows) == [first.id]


@pytest.mark.asyncio
async def test_importer_requires_one_decision_for_every_conflict(tmp_path) -> None:
    from bifrost.manifest import Manifest
    from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview
    from src.services.solutions.workspace_bundle_import import (
        WorkspaceBundleDecisionError,
        WorkspaceBundleImporter,
    )
    from src.services.solutions.workspace_bundle_plan import PlannedWorkspaceBundle

    plan = PlannedWorkspaceBundle(
        preview=WorkspaceBundlePreview(
            preview_token="p", package_name="P", package_sha256="",
            items=[WorkspaceBundleItem(id="entity:app:x", kind="app", name="x", classification="conflict")],
        ), manifest=Manifest(), id_map={}, work_dir=tmp_path,
    )

    with pytest.raises(WorkspaceBundleDecisionError, match="every conflict"):
        await WorkspaceBundleImporter(object()).apply(plan, [])


@pytest.mark.asyncio
async def test_promote_selected_files_streams_source_through_file_index(
    tmp_path, monkeypatch
) -> None:
    from bifrost.manifest import Manifest
    from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview
    from src.services.solutions.workspace_bundle_import import WorkspaceBundleImporter
    from src.services.solutions.workspace_bundle_plan import PlannedWorkspaceBundle

    source = tmp_path / "modules" / "customer.py"
    source.parent.mkdir()
    source.write_bytes(b"value = 1\n")
    plan = PlannedWorkspaceBundle(
        preview=WorkspaceBundlePreview(
            preview_token="p", package_name="P", package_sha256="",
            items=[WorkspaceBundleItem(id="file:modules/customer.py", kind="file", name="modules/customer.py", classification="create")],
        ),
        manifest=Manifest(), id_map={}, work_dir=tmp_path,
        file_hashes={"modules/customer.py": "585c93666fcb046b7b264d3fa73202aa2a38254ae82a4b3ba19e873c2d5a9886"},
    )

    class FileIndex:
        def __init__(self) -> None:
            self.writes: list[tuple[str, str]] = []

        async def write_file(self, path: str, source, *, expected_hash: str) -> str:
            self.writes.append((path, expected_hash))
            return "ignored"

    index = FileIndex()
    class Repo:
        async def content_hash(self, _path):  # noqa: ANN001, ANN201
            return None

    monkeypatch.setattr(
        "src.services.solutions.workspace_bundle_import.RepoStorage", Repo
    )
    promoted = await WorkspaceBundleImporter(object()).promote_selected_files(
        plan,
        {"file:modules/customer.py"},
        file_index=index,
        expected_destination_hashes={},
    )

    assert promoted == ["modules/customer.py"]
    assert index.writes == [("modules/customer.py", "585c93666fcb046b7b264d3fa73202aa2a38254ae82a4b3ba19e873c2d5a9886")]


@pytest.mark.asyncio
async def test_promote_selected_files_rejects_a_destination_changed_after_preview(
    tmp_path, monkeypatch
) -> None:
    """A selected replacement must not overwrite a newer workspace write."""
    from bifrost.manifest import Manifest
    from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview
    from src.services.solutions.workspace_bundle_import import (
        WorkspaceBundleDecisionError,
        WorkspaceBundleImporter,
    )
    from src.services.solutions.workspace_bundle_plan import PlannedWorkspaceBundle

    source = tmp_path / "modules" / "customer.py"
    source.parent.mkdir()
    source.write_bytes(b"value = 2\n")
    plan = PlannedWorkspaceBundle(
        preview=WorkspaceBundlePreview(
            preview_token="p", package_name="P", package_sha256="",
            items=[WorkspaceBundleItem(
                id="file:modules/customer.py", kind="file", name="modules/customer.py",
                classification="conflict",
            )],
        ),
        manifest=Manifest(), id_map={}, work_dir=tmp_path,
        file_hashes={"modules/customer.py": "a" * 64},
    )

    class Repo:
        async def content_hash(self, _path):  # noqa: ANN001, ANN201
            return "newer-destination-hash"

    class FileIndex:
        async def write_file(self, *_args, **_kwargs):  # noqa: ANN001, ANN003, ANN201
            raise AssertionError("changed destination must not be overwritten")

    monkeypatch.setattr(
        "src.services.solutions.workspace_bundle_import.RepoStorage", Repo
    )

    with pytest.raises(WorkspaceBundleDecisionError, match="changed after preview"):
        await WorkspaceBundleImporter(object()).promote_selected_files(
            plan,
            {"file:modules/customer.py"},
            file_index=FileIndex(),
            expected_destination_hashes={"modules/customer.py": "old-preview-hash"},
        )


def test_workspace_bundle_job_reuses_the_shared_workspace_lock() -> None:
    from src.jobs.platform.git_operation import WORKSPACE_MUTATION_RESOURCE_LOCK_KEY
    from src.jobs.platform.registry import get_platform_job_definition
    from src.jobs.platform.workspace_bundle_import import WORKSPACE_BUNDLE_IMPORT_DEFINITION

    assert WORKSPACE_BUNDLE_IMPORT_DEFINITION.job_type == "workspace.bundle_import"
    assert WORKSPACE_BUNDLE_IMPORT_DEFINITION.policy.max_concurrency == 1
    assert WORKSPACE_BUNDLE_IMPORT_DEFINITION.policy.retry_on_runner_loss is True
    assert get_platform_job_definition("workspace.bundle_import") is WORKSPACE_BUNDLE_IMPORT_DEFINITION
    assert WORKSPACE_MUTATION_RESOURCE_LOCK_KEY == "workspace"


@pytest.mark.asyncio
async def test_promoted_python_refreshes_module_cache_and_oversized_text_removes_stale_index(tmp_path, monkeypatch) -> None:
    """The file phase must never leave search or module reads on old content."""
    from src.services import file_index_service as index_module
    from src.services.file_index_service import MAX_INDEXABLE_TEXT_BYTES, FileIndexService

    class Storage:
        async def put_object_from_chunks(self, _key, chunks):
            import hashlib
            digest = hashlib.sha256()
            size = 0
            async for chunk in chunks:
                digest.update(chunk)
                size += len(chunk)
            return digest.hexdigest(), size

    class Repo:
        _settings = object()

        def _repo_key(self, path):
            return path

        async def write(self, _path, content):
            import hashlib
            return hashlib.sha256(content).hexdigest()

    cached = AsyncMock()
    invalidated = AsyncMock()
    monkeypatch.setattr(index_module, "S3StorageClient", lambda _settings: Storage())
    monkeypatch.setattr("src.core.module_cache.set_module", cached)
    monkeypatch.setattr("src.core.module_cache.invalidate_module", invalidated)
    db = type("Db", (), {"execute": AsyncMock()})()
    service = FileIndexService(db, Repo())
    source = tmp_path / "module.py"
    source.write_text("answer = 42\n")
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    await service.write_file("modules/module.py", source, expected_hash=digest)

    cached.assert_awaited_once_with("modules/module.py", "answer = 42\n", digest)
    oversized = tmp_path / "large.py"
    with oversized.open("wb") as handle:
        handle.truncate(MAX_INDEXABLE_TEXT_BYTES + 1)
    with oversized.open("rb") as handle:
        oversized_digest = hashlib.file_digest(handle, "sha256").hexdigest()
    await service.write_file(
        "modules/large.py", oversized,
        expected_hash=oversized_digest,
    )
    invalid = tmp_path / "invalid.py"
    invalid.write_bytes(b"\xff")
    invalid_digest = hashlib.sha256(invalid.read_bytes()).hexdigest()
    await service.write_file("modules/invalid.py", invalid, expected_hash=invalid_digest)

    assert db.execute.await_count == 3
    invalidated.assert_has_awaits([
        call("modules/large.py"),
        call("modules/invalid.py"),
    ])
