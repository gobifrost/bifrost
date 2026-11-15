"""Durability contracts for the workspace-bundle platform job."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _context(user_id: str = "owner", job_id=None):
    return SimpleNamespace(requested_by_user_id=user_id, job_id=job_id, organization_id=None)


def _payload():
    from src.jobs.platform.workspace_bundle_import import WorkspaceBundleImportPayload

    return WorkspaceBundleImportPayload(preview_id=uuid4(), package_sha256="a" * 64, decisions=[])


def test_workspace_bundle_job_allows_cancellation_before_commit() -> None:
    from src.jobs.platform.workspace_bundle_import import WORKSPACE_BUNDLE_IMPORT_DEFINITION

    assert WORKSPACE_BUNDLE_IMPORT_DEFINITION.policy.allow_running_cancellation is True
    assert WORKSPACE_BUNDLE_IMPORT_DEFINITION.policy.retry_on_failure is True


def test_workspace_bundle_preview_guard_rejects_other_requester() -> None:
    from src.jobs.platform.workspace_bundle_import import _require_requester
    from src.jobs.platform.base import PlatformJobFailure

    payload = _payload()
    with pytest.raises(PlatformJobFailure, match="another user"):
        _require_requester(
            {"requested_by": "other", "package_sha256": payload.package_sha256,
             "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()},
            _context(), payload,
        )


def test_workspace_bundle_preview_guard_rejects_expired_preview() -> None:
    from src.jobs.platform.workspace_bundle_import import _require_requester
    from src.jobs.platform.base import PlatformJobFailure

    payload = _payload()
    with pytest.raises(PlatformJobFailure, match="expired"):
        _require_requester(
            {"requested_by": "owner", "package_sha256": payload.package_sha256,
             "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
            _context(), payload,
        )


def test_workspace_bundle_accepted_job_remains_valid_after_preview_ttl() -> None:
    from src.jobs.platform.workspace_bundle_import import _require_requester

    payload = _payload()
    job_id = uuid4()
    _require_requester(
        {
            "requested_by": "owner",
            "package_sha256": payload.package_sha256,
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            "platform_job_id": str(job_id),
        },
        _context(job_id=job_id),
        payload,
    )


def test_workspace_bundle_revalidation_rejects_changed_natural_key_or_file_fingerprint() -> None:
    from src.jobs.platform.base import PlatformJobFailure
    from src.jobs.platform.workspace_bundle_import import _require_preview_is_current
    from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview

    reviewed = WorkspaceBundlePreview(
        preview_token="preview", package_name="P", package_sha256="a" * 64,
        items=[WorkspaceBundleItem(
            id="entity:workflow:one", kind="workflow", name="one", classification="create",
            match_key="('workflows/one.py', 'one')", source_id=uuid4(), target_id=uuid4(),
        )],
    )
    current = reviewed.model_copy(update={
        "items": [reviewed.items[0].model_copy(update={"classification": "conflict"})],
    })

    with pytest.raises(PlatformJobFailure, match="changed since preview") as error:
        _require_preview_is_current(reviewed, {"workflows/one.py": "old"}, current, {"workflows/one.py": "new"})

    assert error.value.code == "preview_stale"


def test_workspace_bundle_precommit_checkpoint_recognizes_its_committed_selection() -> None:
    from src.jobs.platform.workspace_bundle_import import _checkpoint_matches_replanned_bundle
    from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview

    item = WorkspaceBundleItem(
        id="entity:workflow:one", kind="workflow", name="one", classification="create",
        match_key="('workflows/one.py', 'one')", source_id=uuid4(), target_id=uuid4(),
    )
    reviewed = WorkspaceBundlePreview(
        preview_token="preview", package_name="P", package_sha256="a" * 64, items=[item],
    )
    committed = reviewed.model_copy(update={
        "items": [item.model_copy(update={"classification": "unchanged"})],
    })

    assert _checkpoint_matches_replanned_bundle(
        reviewed, committed, {"entity:workflow:one"},
    )


def _job_metadata(payload) -> dict:
    source_id = uuid4()
    target_id = uuid4()
    return {
        "requested_by": "owner",
        "package_sha256": payload.package_sha256,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        "preview": {
            "preview_token": str(payload.preview_id), "package_name": "P",
            "package_sha256": payload.package_sha256,
            "items": [{
                "id": "entity:workflow:one", "kind": "workflow", "name": "one",
                "classification": "create", "match_key": "('workflows/one.py', 'one')",
                "source_id": str(source_id), "target_id": str(target_id),
            }],
        },
        "manifest": {}, "id_map": {}, "file_hashes": {},
        "destination_file_hashes": {},
    }


def _install_job_doubles(
    monkeypatch, payload, *, fail_finalize: bool, cancel_before_commit: bool = False,
    lose_after_db_commit: bool = False, destination_changed: bool = False,
    lose_during_compute: bool = False, lose_during_promotion: bool = False,
):
    """Replace only process/external boundaries; exercise the real job phases."""
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.solutions.workspace_bundle_import import WorkspaceBundleImportResult
    from src.jobs.platform import workspace_bundle_import as job_module

    class Storage:
        metadata = _job_metadata(payload)
        deletes = 0

        def __init__(self, _preview_id):
            pass

        async def load_metadata(self):
            return self.metadata

        async def copy_package_to(self, destination, *, expected_sha256):
            destination.write_bytes(b"archive")
            return len(b"archive")

        async def delete(self):
            type(self).deletes += 1

    class Db:
        entity_changes_committed = False
        saved_checkpoint = None

        def __init__(self):
            self.commits = 0
            self.pending_apply = False
            self.pending_checkpoint = None

        async def commit(self):
            self.commits += 1
            if self.pending_apply:
                type(self).entity_changes_committed = True
            if self.pending_checkpoint is not None:
                type(self).saved_checkpoint = self.pending_checkpoint

    used_dbs: list[Db] = []
    active_scopes = 0

    @asynccontextmanager
    async def db_context():
        nonlocal active_scopes
        db = Db()
        used_dbs.append(db)
        active_scopes += 1
        try:
            yield db
        finally:
            active_scopes -= 1

    class Importer:
        apply_calls = 0
        promote_calls = 0
        promotion_preconditions: list[dict[str, str]] = []

        def __init__(self, _db, *, progress_fn=None):
            self.db = _db
            self.progress_fn = progress_fn

        async def apply(self, _plan, _decisions, *, config_values, updated_by):
            type(self).apply_calls += 1
            self.db.pending_apply = True
            if self.progress_fn is not None:
                await self.progress_fn("Importing synthetic entities", 1, 1)
            return WorkspaceBundleImportResult(
                imported_item_ids=frozenset({"entity:workflow:one"}),
                selected_item_ids=frozenset({"entity:workflow:one"}), operations=(),
            )

        async def promote_selected_files(
            self, _plan, _selected, *, file_index, expected_destination_hashes
        ):
            from src.services.solutions.workspace_bundle_import import WorkspaceBundleDecisionError

            assert expected_destination_hashes == {}
            type(self).promotion_preconditions.append(expected_destination_hashes)
            type(self).promote_calls += 1
            if destination_changed:
                raise WorkspaceBundleDecisionError(
                    "workspace file modules/one.py changed after preview; create a new preview"
                )
            if fail_finalize and type(self).promote_calls == 1:
                raise RuntimeError("object storage unavailable")
            if lose_during_promotion:
                Context.authorized = False
            return ["modules/one.py"]

    class Writer:
        regenerate_calls = 0

        def __init__(self, _db):
            pass

        async def regenerate_manifest(self):
            type(self).regenerate_calls += 1

    dirty_calls: list[bool] = []

    async def mark_dirty():
        dirty_calls.append(True)

    class Projection:
        @staticmethod
        def from_preview(*_args, **_kwargs):
            return object()

    class Planner:
        def __init__(self, _db, *, preview_id, organization_id=None):
            self.preview_id = preview_id
            self.organization_id = organization_id

        async def plan(self, _projection):
            preview = job_module.WorkspaceBundlePreview.model_validate(Storage.metadata["preview"])
            if Db.entity_changes_committed:
                preview = preview.model_copy(update={
                    "items": [preview.items[0].model_copy(update={"classification": "unchanged"})],
                })
            return SimpleNamespace(
                preview=preview,
                file_hashes=Storage.metadata["file_hashes"],
            )

    class Context:
        authorized = True
        storage = Storage

        def __init__(self, checkpoint=None, crash_after_db_commit=lose_after_db_commit):
            self.requested_by_user_id = "owner"
            self.organization_id = None
            self.file_compute = object()
            self.requested_by_email = "owner@example.invalid"
            self.job_id = uuid4()
            self.checkpoint = checkpoint
            self.crash_after_db_commit = crash_after_db_commit
            self.reports: list[str] = []

        async def report(self, phase, **_kwargs):
            self.reports.append(phase)
            if self.crash_after_db_commit and phase == "Database import committed; finalizing files":
                raise RuntimeError("runner lost after database commit")

        @property
        def saved(self):
            return Db.saved_checkpoint

        async def authorize_effect(self, db):
            return self.authorized and not (cancel_before_commit and db.pending_apply)

        async def checkpoint_in_transaction(self, db, result, *, phase):
            if not await self.authorize_effect(db):
                raise PlatformJobCancelled
            db.pending_checkpoint = result

    @asynccontextmanager
    async def inspector(path, compute):
        # No database transaction is retained across physical computation.
        assert active_scopes == 0
        if lose_during_compute:
            Context.authorized = False
        yield SimpleNamespace(workspace=path.parent / "source", preview=object())

    monkeypatch.setattr(job_module, "WorkspaceBundleStorage", Storage)
    monkeypatch.setattr(job_module, "get_db_context", db_context)
    monkeypatch.setattr(job_module, "WorkspaceBundleImporter", Importer)
    monkeypatch.setattr(job_module, "RepoSyncWriter", Writer)
    monkeypatch.setattr(job_module, "mark_repo_dirty", mark_dirty)
    monkeypatch.setattr(job_module, "SolutionPackageWorkspaceProjection", Projection)
    monkeypatch.setattr(job_module, "WorkspaceBundlePlanner", Planner)
    monkeypatch.setattr(job_module, "inspect_solution_archive", inspector)
    return Context, used_dbs, Importer, Writer, dirty_calls


@pytest.mark.asyncio
async def test_finalize_failure_checkpoints_after_commit_and_retry_resumes_without_reapplying(monkeypatch) -> None:
    from src.jobs.platform.base import PlatformJobFailure
    from src.jobs.platform.workspace_bundle_import import WorkspaceBundleImportPayload, run_workspace_bundle_import

    payload = WorkspaceBundleImportPayload(preview_id=uuid4(), package_sha256="a" * 64, decisions=[])
    Context, used_dbs, Importer, Writer, dirty_calls = _install_job_doubles(
        monkeypatch, payload, fail_finalize=True,
    )
    first = Context()

    with pytest.raises(PlatformJobFailure, match="database changes were committed") as failure:
        await run_workspace_bundle_import(first, payload)

    assert failure.value.retryable is True
    assert first.saved == failure.value.result
    assert sum(db.commits for db in used_dbs) == 1
    assert dirty_calls == []

    retry = Context(checkpoint=first.saved, crash_after_db_commit=False)
    result = await run_workspace_bundle_import(retry, payload)

    assert Importer.apply_calls == 1
    assert Importer.promote_calls == 2
    assert Importer.promotion_preconditions == [{}, {}]
    assert Writer.regenerate_calls == 1
    assert result["promoted_files"] == ["modules/one.py"]
    assert dirty_calls == [True]


@pytest.mark.asyncio
async def test_destination_changed_after_preview_is_not_retried(monkeypatch) -> None:
    from src.jobs.platform.base import PlatformJobFailure
    from src.jobs.platform.workspace_bundle_import import WorkspaceBundleImportPayload, run_workspace_bundle_import

    payload = WorkspaceBundleImportPayload(preview_id=uuid4(), package_sha256="a" * 64, decisions=[])
    Context, used_dbs, Importer, _Writer, dirty_calls = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False, destination_changed=True,
    )

    with pytest.raises(PlatformJobFailure, match="changed after preview") as failure:
        await run_workspace_bundle_import(Context(), payload)

    assert failure.value.code == "workspace_bundle_file_precondition_failed"
    assert failure.value.retryable is False
    assert Importer.promote_calls == 1
    assert sum(db.commits for db in used_dbs) == 1
    assert dirty_calls == []


@pytest.mark.asyncio
async def test_cancellation_requested_before_commit_leaves_database_uncommitted(monkeypatch) -> None:
    from src.jobs.platform.base import PlatformJobCancelled
    from src.jobs.platform.workspace_bundle_import import WorkspaceBundleImportPayload, run_workspace_bundle_import

    payload = WorkspaceBundleImportPayload(preview_id=uuid4(), package_sha256="a" * 64, decisions=[])
    Context, used_dbs, _Importer, _Writer, dirty_calls = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False, cancel_before_commit=True,
    )

    with pytest.raises(PlatformJobCancelled):
        await run_workspace_bundle_import(Context(), payload)

    assert sum(db.commits for db in used_dbs) == 0
    assert dirty_calls == []


@pytest.mark.asyncio
async def test_runner_loss_after_database_commit_resumes_from_atomic_checkpoint(monkeypatch) -> None:
    from src.jobs.platform.workspace_bundle_import import WorkspaceBundleImportPayload, run_workspace_bundle_import

    payload = WorkspaceBundleImportPayload(preview_id=uuid4(), package_sha256="a" * 64, decisions=[])
    Context, used_dbs, Importer, Writer, dirty_calls = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False, lose_after_db_commit=True,
    )
    first = Context()

    with pytest.raises(RuntimeError, match="runner lost"):
        await run_workspace_bundle_import(first, payload)

    assert first.saved["db_apply_started"] is True
    assert first.saved["db_applied"] is True
    assert sum(db.commits for db in used_dbs) == 1

    retry = Context(checkpoint=first.saved, crash_after_db_commit=False)
    result = await run_workspace_bundle_import(retry, payload)

    assert Importer.apply_calls == 1
    assert Writer.regenerate_calls == 1
    assert dirty_calls == [True]
    assert result["promoted_files"] == ["modules/one.py"]


@pytest.mark.asyncio
async def test_workspace_import_refuses_missing_compute_without_loading_archive(monkeypatch):
    from src.jobs.platform import workspace_bundle_import as module
    from src.jobs.platform.base import PlatformJobFailure
    from unittest.mock import Mock

    storage = Mock(side_effect=AssertionError("No archive may be loaded"))
    monkeypatch.setattr(module, "WorkspaceBundleStorage", storage)
    with pytest.raises(PlatformJobFailure) as failure:
        await module.run_workspace_bundle_import(SimpleNamespace(file_compute=None), _payload())
    assert failure.value.code == "file_compute_required"
    storage.assert_not_called()


def test_workspace_preview_cannot_change_scope_or_borrow_another_job():
    from src.jobs.platform.workspace_bundle_import import _require_requester
    from src.jobs.platform.base import PlatformJobFailure
    payload = _payload()
    context = _context(job_id=uuid4())
    metadata = _job_metadata(payload)
    for changes in ({"organization_id": str(uuid4())}, {"platform_job_id": str(uuid4())}):
        with pytest.raises(PlatformJobFailure) as failure:
            _require_requester(metadata | changes, context, payload)
        assert failure.value.code == "preview_not_authorized"


@pytest.mark.asyncio
async def test_owner_loss_during_compute_cannot_apply_or_delete_input(monkeypatch):
    from src.jobs.platform.base import PlatformJobCancelled
    from src.jobs.platform.workspace_bundle_import import run_workspace_bundle_import
    payload = _payload()
    Context, dbs, Importer, Writer, dirty = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False, lose_during_compute=True,
    )
    with pytest.raises(PlatformJobCancelled):
        await run_workspace_bundle_import(Context(), payload)
    assert Importer.apply_calls == Importer.promote_calls == Writer.regenerate_calls == 0
    assert sum(db.commits for db in dbs) == 0
    assert not dirty and Context.storage.deletes == 0


@pytest.mark.asyncio
async def test_owner_loss_after_file_promotion_preserves_retry_checkpoint_and_input(monkeypatch):
    from src.jobs.platform.base import PlatformJobCancelled
    from src.jobs.platform.workspace_bundle_import import run_workspace_bundle_import
    payload = _payload()
    Context, dbs, Importer, Writer, dirty = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False, lose_during_promotion=True,
    )
    context = Context()
    with pytest.raises(PlatformJobCancelled):
        await run_workspace_bundle_import(context, payload)
    assert context.saved["db_applied"] is True
    assert Importer.apply_calls == Importer.promote_calls == 1
    assert Writer.regenerate_calls == 0
    assert sum(db.commits for db in dbs) == 1
    assert not dirty and Context.storage.deletes == 0


@pytest.mark.asyncio
async def test_completed_import_resumes_after_input_cleanup_without_recomputing(monkeypatch):
    from src.jobs.platform import workspace_bundle_import as module
    from unittest.mock import AsyncMock
    payload = _payload()
    Context, dbs, Importer, Writer, dirty = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False,
    )
    first = Context()
    expected = await module.run_workspace_bundle_import(first, payload)
    assert first.saved["files_finalized"] is True
    # A repeated delivery cannot load/reinspect staging that was already deleted.
    async def unavailable(_self):
        raise AssertionError("Completed import must not reload preview metadata")
    monkeypatch.setattr(Context.storage, "load_metadata", unavailable)
    inspector = AsyncMock(side_effect=AssertionError("Completed import must not compute again"))
    monkeypatch.setattr(module, "inspect_solution_archive", inspector)
    resumed = await module.run_workspace_bundle_import(Context(checkpoint=first.saved), payload)
    assert resumed == expected
    assert Importer.apply_calls == Importer.promote_calls == Writer.regenerate_calls == 1
    assert Context.storage.deletes == 2
    inspector.assert_not_called()


@pytest.mark.asyncio
async def test_completion_checkpoint_remains_requester_and_digest_bound(monkeypatch):
    from src.jobs.platform import workspace_bundle_import as module
    from src.jobs.platform.base import PlatformJobFailure
    payload = _payload()
    Context, _dbs, _Importer, _Writer, _dirty = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False,
    )
    completed = {"files_finalized": True, "preview_id": str(payload.preview_id),
                 "package_sha256": payload.package_sha256, "requested_by": "someone-else",
                 "organization_id": None}
    with pytest.raises(PlatformJobFailure) as error:
        await module.run_workspace_bundle_import(Context(checkpoint=completed), payload)
    assert error.value.code == "workspace_checkpoint_mismatch"
    assert Context.storage.deletes == 0
