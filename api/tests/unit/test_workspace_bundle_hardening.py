"""Durability contracts for the workspace-bundle platform job."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _context(user_id: str = "owner"):
    return SimpleNamespace(requested_by_user_id=user_id)


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


def _job_metadata(payload) -> dict:
    return {
        "requested_by": "owner",
        "package_sha256": payload.package_sha256,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        "preview": {"preview_token": str(payload.preview_id), "package_name": "P", "package_sha256": payload.package_sha256, "items": []},
        "manifest": {}, "id_map": {}, "file_hashes": {},
    }


def _install_job_doubles(monkeypatch, payload, *, fail_finalize: bool, cancel_before_commit: bool = False):
    """Replace only process/external boundaries; exercise the real job phases."""
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.solutions.workspace_bundle_import import WorkspaceBundleImportResult
    import src.jobs.platform.workspace_bundle_import as job_module
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
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

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

        def __init__(self, _db, *, progress_fn=None):
            pass

        async def apply(self, _plan, _decisions):
            type(self).apply_calls += 1
            return WorkspaceBundleImportResult(
                imported_item_ids=frozenset({"entity:workflow:one"}),
                selected_item_ids=frozenset({"file:modules/one.py"}), operations=(),
            )

        async def promote_selected_files(self, _plan, _selected, *, file_index):
            type(self).promote_calls += 1
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

    class Context:
        def __init__(self, checkpoint=None):
            self.requested_by_user_id = "owner"
            self.checkpoint = checkpoint
            self.saved = None
            self.reports: list[str] = []

        async def report(self, phase, **_kwargs):
            self.reports.append(phase)
            if cancel_before_commit and phase == "Committing workspace entity changes":
                raise PlatformJobCancelled

        async def save_checkpoint(self, result, *, phase):
            self.saved = result

    monkeypatch.setattr(job_module, "WorkspaceBundleStorage", Storage)
    monkeypatch.setattr(job_module, "get_db_context", db_context)
    monkeypatch.setattr(job_module, "WorkspaceBundleImporter", Importer)
    monkeypatch.setattr(job_module, "RepoSyncWriter", Writer)
    monkeypatch.setattr(job_module, "mark_repo_dirty", mark_dirty)
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

    retry = Context(checkpoint=first.saved)
    result = await run_workspace_bundle_import(retry, payload)

    assert Importer.apply_calls == 1
    assert Importer.promote_calls == 2
    assert Writer.regenerate_calls == 1
    assert result["promoted_files"] == ["modules/one.py"]
    assert dirty_calls == [True]


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
