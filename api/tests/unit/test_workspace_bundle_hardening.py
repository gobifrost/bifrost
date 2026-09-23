"""Durability contracts for the workspace-bundle platform job."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _context(user_id: str = "owner", job_id=None):
    return SimpleNamespace(requested_by_user_id=user_id, job_id=job_id)


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
):
    """Replace only process/external boundaries; exercise the real job phases."""
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.solutions.workspace_bundle_import import WorkspaceBundleImportResult
    from src.jobs.platform import workspace_bundle_import as job_module
    import src.services.solutions.zip_install as zip_install

    class Storage:
        metadata = _job_metadata(payload)

        def __init__(self, _preview_id):
            pass

        async def load_metadata(self):
            return self.metadata

        async def copy_package_to(self, destination, *, expected_sha256):
            destination.write_bytes(b"archive")
            return len(b"archive")

        async def delete(self):
            return None

    class Db:
        entity_changes_committed = False

        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1
            type(self).entity_changes_committed = True

    dbs = [Db(), Db(), Db()]
    used_dbs: list[Db] = []

    @asynccontextmanager
    async def db_context():
        db = dbs.pop(0)
        used_dbs.append(db)
        yield db

    class Importer:
        apply_calls = 0
        promote_calls = 0
        promotion_preconditions: list[dict[str, str]] = []

        def __init__(self, _db, *, progress_fn=None):
            pass

        async def apply(self, _plan, _decisions, *, config_values, updated_by):
            type(self).apply_calls += 1
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
        def __init__(self, checkpoint=None, crash_after_db_commit=lose_after_db_commit):
            self.requested_by_user_id = "owner"
            self.requested_by_email = "owner@example.invalid"
            self.job_id = uuid4()
            self.checkpoint = checkpoint
            self.crash_after_db_commit = crash_after_db_commit
            self.saved = None
            self.reports: list[str] = []

        async def report(self, phase, **_kwargs):
            self.reports.append(phase)
            if cancel_before_commit and phase == "Committing workspace entity changes":
                raise PlatformJobCancelled

        async def save_checkpoint(self, result, *, phase):
            if self.crash_after_db_commit and phase == "Database import committed; finalizing files":
                raise RuntimeError("runner lost after database commit")
            self.saved = result

    monkeypatch.setattr(job_module, "WorkspaceBundleStorage", Storage)
    monkeypatch.setattr(job_module, "get_db_context", db_context)
    monkeypatch.setattr(job_module, "WorkspaceBundleImporter", Importer)
    monkeypatch.setattr(job_module, "RepoSyncWriter", Writer)
    monkeypatch.setattr(job_module, "mark_repo_dirty", mark_dirty)
    monkeypatch.setattr(job_module, "SolutionPackageWorkspaceProjection", Projection)
    monkeypatch.setattr(job_module, "WorkspaceBundlePlanner", Planner)
    monkeypatch.setattr(zip_install, "_safe_extract_path", lambda *_args: None)
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
    assert used_dbs[0].commits == 1
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
    assert used_dbs[0].commits == 1
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

    assert used_dbs[0].commits == 0
    assert dirty_calls == []


@pytest.mark.asyncio
async def test_runner_loss_after_database_commit_resumes_finalization_from_precommit_checkpoint(monkeypatch) -> None:
    from src.jobs.platform.workspace_bundle_import import WorkspaceBundleImportPayload, run_workspace_bundle_import

    payload = WorkspaceBundleImportPayload(preview_id=uuid4(), package_sha256="a" * 64, decisions=[])
    Context, used_dbs, Importer, Writer, dirty_calls = _install_job_doubles(
        monkeypatch, payload, fail_finalize=False, lose_after_db_commit=True,
    )
    first = Context()

    with pytest.raises(RuntimeError, match="runner lost"):
        await run_workspace_bundle_import(first, payload)

    assert first.saved["db_apply_started"] is True
    assert "db_applied" not in first.saved
    assert used_dbs[0].commits == 1

    retry = Context(checkpoint=first.saved, crash_after_db_commit=False)
    result = await run_workspace_bundle_import(retry, payload)

    assert Importer.apply_calls == 1
    assert Writer.regenerate_calls == 1
    assert dirty_calls == [True]
    assert result["promoted_files"] == ["modules/one.py"]
