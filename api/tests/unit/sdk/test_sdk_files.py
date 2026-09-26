"""Focused service tests for the cloud-mode SDK file read extraction.

Covers the shared service (``shared.sdk_files``) used by the HTTP file
routes for ``files.read``/``read_bytes``, ``files.list`` (without
``include_metadata``), ``files.exists``, and ``files.stat``:

- own/Solution/org/global tier order (first hit wins, in order)
- policy allow/deny and the final-deny audit (404 misses never audit)
- declared Solution file locations and the workspace-in-solution gate
- binary content handling
- list filtering (primary always enumerated, fallback tiers gated)
- existence/stat semantics (never 403/404 — just False/absent)

Mutation coverage (``files.write``/``files.delete`` and signed URLs) lives
in the ``TestSdkWriteFile``, ``TestSdkDeleteFile``, and ``TestSdkSignedUrl``
classes below: success/conflict semantics, policy-deny audit,
metadata/commit/publish order, local-mode side effects, signed GET tier
selection, and signed PUT policy/presign.
"""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from shared.file_access import FileCaller, FileServiceError
from shared.file_paths import resolve_s3_key
from shared.sdk_files import (
    sdk_delete_file,
    sdk_file_exists,
    sdk_file_stat,
    sdk_list_files,
    sdk_read_file,
    sdk_signed_url,
    sdk_write_file,
)
from src.core.principal import UserPrincipal
from src.services.solution_scope import FileTier

ORG_ID = UUID("22222222-2222-2222-2222-222222222222")
SOLUTION_ID = UUID("11111111-1111-1111-1111-111111111111")

TIERS_SOLUTION_FIRST = [
    FileTier("solution", str(SOLUTION_ID), ORG_ID, SOLUTION_ID),
    FileTier("org", str(ORG_ID), ORG_ID, None),
    FileTier("global", "global", None, None),
]


def _user(**kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="reader@example.com",
        organization_id=kwargs.get("organization_id", ORG_ID),
        name="Reader",
        is_active=True,
        is_superuser=kwargs.get("is_superuser", False),
        is_verified=True,
    )


def _caller(**kwargs) -> FileCaller:
    db = MagicMock()
    db.commit = AsyncMock()
    return FileCaller(
        user=kwargs.get("user", _user()),
        db=db,
        org_id=kwargs.get("org_id", ORG_ID),
        solution_id=kwargs.get("solution_id"),
        caller_solution_id=kwargs.get("caller_solution_id"),
        app_id=kwargs.get("app_id"),
    )


def _backend(monkeypatch, *, files: dict[tuple[str, str | None], bytes]) -> MagicMock:
    """Fake cloud backend backed by a {(path, scope): content} map."""
    backend = MagicMock()

    async def _read(path, location, scope=None):
        try:
            return files[(path, scope)]
        except KeyError:
            raise FileNotFoundError(f"File not found: {path}")

    async def _exists(path, location, scope=None):
        return (path, scope) in files

    async def _list(directory, location, scope=None):
        prefix = f"{directory}/" if directory else ""
        return sorted(
            path for (path, tier_scope) in files if tier_scope == scope and path.startswith(prefix)
        )

    backend.read = AsyncMock(side_effect=_read)
    backend.exists = AsyncMock(side_effect=_exists)
    backend.list = AsyncMock(side_effect=_list)
    monkeypatch.setattr("src.services.file_backend.get_backend", lambda *args: backend)
    return backend


def _tiers(monkeypatch, tiers: list[FileTier]) -> None:
    monkeypatch.setattr(
        "src.services.solution_scope.file_read_tiers",
        AsyncMock(return_value=tiers),
    )


def _allow_all(monkeypatch) -> None:
    monkeypatch.setattr(
        "shared.sdk_files.authorize_file_policy",
        AsyncMock(return_value=True),
    )


def _deny_all(monkeypatch) -> None:
    monkeypatch.setattr(
        "shared.sdk_files.authorize_file_policy",
        AsyncMock(return_value=False),
    )


@pytest.mark.asyncio
async def test_read_prefers_solution_tier_over_org_and_global(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _allow_all(monkeypatch)
    backend = _backend(
        monkeypatch,
        files={
            ("doc.txt", str(SOLUTION_ID)): b"own",
            ("doc.txt", str(ORG_ID)): b"org",
            ("doc.txt", "global"): b"global",
        },
    )
    result = await sdk_read_file(
        _caller(), path="doc.txt", location="reports", scope=None
    )
    assert result.content == b"own"
    assert result.binary is False
    assert backend.read.await_args_list[0].args == ("doc.txt", "reports")
    assert backend.read.await_args_list[0].kwargs["scope"] == str(SOLUTION_ID)


@pytest.mark.asyncio
async def test_read_falls_through_tiers_in_order(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _allow_all(monkeypatch)
    backend = _backend(
        monkeypatch, files={("doc.txt", "global"): b"shared"}
    )
    result = await sdk_read_file(
        _caller(), path="doc.txt", location="reports", scope=None
    )
    assert result.content == b"shared"
    assert [c.kwargs["scope"] for c in backend.read.await_args_list] == [
        str(SOLUTION_ID),
        str(ORG_ID),
        "global",
    ]


@pytest.mark.asyncio
async def test_read_missing_across_allowed_tiers_is_404_without_audit(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _allow_all(monkeypatch)
    _backend(monkeypatch, files={})
    emit = AsyncMock()
    monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)
    with pytest.raises(FileServiceError) as exc:
        await sdk_read_file(_caller(), path="gone.txt", location="reports", scope=None)
    assert exc.value.status_code == 404
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_denied_in_all_tiers_is_403_with_audit(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _deny_all(monkeypatch)
    _backend(monkeypatch, files={("doc.txt", "global"): b"shared"})
    emit = AsyncMock()
    monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)
    caller = _caller()
    with pytest.raises(FileServiceError) as exc:
        await sdk_read_file(caller, path="doc.txt", location="reports", scope=None)
    assert exc.value.status_code == 403
    assert exc.value.detail["action"] == "read"
    emit.assert_awaited_once()
    caller.db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_binary_bytes_returned_raw(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST[:1])
    _allow_all(monkeypatch)
    _backend(
        monkeypatch, files={("img.bin", str(SOLUTION_ID)): b"\xff\x00\x01binary"}
    )
    result = await sdk_read_file(
        _caller(), path="img.bin", location="reports", scope=None, binary=True
    )
    assert result.content == b"\xff\x00\x01binary"
    assert result.binary is True


@pytest.mark.asyncio
async def test_read_binary_without_flag_is_400(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST[:1])
    _allow_all(monkeypatch)
    _backend(
        monkeypatch, files={("img.bin", str(SOLUTION_ID)): b"\xff\x00\x01binary"}
    )
    with pytest.raises(FileServiceError) as exc:
        await sdk_read_file(
            _caller(), path="img.bin", location="reports", scope=None, binary=False
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_read_undeclared_solution_location_is_404(monkeypatch) -> None:
    caller = _caller(solution_id=str(SOLUTION_ID))
    monkeypatch.setattr(
        "src.services.solution_scope.solution_declares_file_location",
        AsyncMock(return_value=False),
    )
    with pytest.raises(FileServiceError) as exc:
        await sdk_read_file(caller, path="doc.txt", location="reports", scope=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_read_workspace_in_solution_context_is_400(monkeypatch) -> None:
    caller = _caller(solution_id=str(SOLUTION_ID))

    async def _boom(db, ctx, location, requested_scope):
        raise ValueError("workspace is not available in solution file context")

    monkeypatch.setattr("src.services.solution_scope.file_read_tiers", _boom)
    with pytest.raises(FileServiceError) as exc:
        await sdk_read_file(caller, path="doc.txt", location="workspace", scope=None)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_primary_enumerated_fallback_gated_by_directory_policy(
    monkeypatch,
) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _backend(
        monkeypatch,
        files={
            ("a.txt", str(SOLUTION_ID)): b"own-a",
            ("shared.txt", str(SOLUTION_ID)): b"own-shared",
            ("shared.txt", "global"): b"global-shared",
            ("b.txt", "global"): b"global-b",
        },
    )

    async def _authorize(caller, *, action, location, scope, path, **kwargs):
        # Directory listable in the own tier only; per-file read open.
        if action == "list" and "/" not in path:
            return scope == str(SOLUTION_ID)
        return True

    monkeypatch.setattr(
        "shared.sdk_files.authorize_file_policy", AsyncMock(side_effect=_authorize)
    )
    # Per-file filtering runs inside shared.file_access (bound separately).
    monkeypatch.setattr(
        "shared.file_access.authorize_file_policy", AsyncMock(side_effect=_authorize)
    )
    files = await sdk_list_files(
        _caller(), directory="", location="reports", scope=None
    )
    # Fallback global tier hidden (directory denied there); own tier kept,
    # per-file filtered. No duplicates across tiers.
    assert files == ["a.txt", "shared.txt"]


@pytest.mark.asyncio
async def test_list_denied_everywhere_is_403_with_audit(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _deny_all(monkeypatch)
    _backend(monkeypatch, files={})
    emit = AsyncMock()
    monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)
    caller = _caller()
    with pytest.raises(FileServiceError) as exc:
        await sdk_list_files(caller, directory="", location="reports", scope=None)
    assert exc.value.status_code == 403
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_exists_true_only_when_allowed_and_present(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _backend(monkeypatch, files={("doc.txt", "global"): b"shared"})
    caller = _caller()

    async def _authorize(caller_arg, *, action, location, scope, path, **kwargs):
        return scope == "global"

    monkeypatch.setattr(
        "shared.sdk_files.authorize_file_policy", AsyncMock(side_effect=_authorize)
    )
    assert await sdk_file_exists(caller, path="doc.txt", location="reports", scope=None) is True
    assert await sdk_file_exists(caller, path="gone.txt", location="reports", scope=None) is False


@pytest.mark.asyncio
async def test_exists_denied_means_false_not_error(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _deny_all(monkeypatch)
    backend = _backend(monkeypatch, files={("doc.txt", "global"): b"shared"})
    assert await sdk_file_exists(
        _caller(), path="doc.txt", location="reports", scope=None
    ) is False
    backend.exists.assert_not_awaited()


@pytest.mark.asyncio
async def test_stat_returns_version_and_size(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _allow_all(monkeypatch)
    _backend(monkeypatch, files={("doc.txt", str(SOLUTION_ID)): b"hello"})
    stat = await sdk_file_stat(
        _caller(), path="doc.txt", location="reports", scope=None
    )
    assert stat.exists is True
    assert stat.version == "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert stat.size == 5


@pytest.mark.asyncio
async def test_stat_missing_is_absent_not_error(monkeypatch) -> None:
    _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
    _allow_all(monkeypatch)
    _backend(monkeypatch, files={})
    stat = await sdk_file_stat(
        _caller(), path="gone.txt", location="reports", scope=None
    )
    assert stat.exists is False
    assert stat.path == "gone.txt"


@pytest.mark.asyncio
async def test_from_context_carries_trusted_principal_fields() -> None:
    ctx = MagicMock()
    ctx.user = _user()
    ctx.db = MagicMock()
    ctx.org_id = ORG_ID
    ctx.solution_id = str(SOLUTION_ID)
    ctx.caller_solution_id = "caller-install"
    ctx.app_id = "app-1"
    caller = FileCaller.from_context(ctx)
    assert caller.user is ctx.user
    assert caller.db is ctx.db
    assert caller.org_id == ORG_ID
    assert caller.solution_id == str(SOLUTION_ID)
    assert caller.caller_solution_id == "caller-install"
    assert caller.app_id == "app-1"


@pytest.mark.asyncio
async def test_http_read_adapter_encodes_and_maps_errors(monkeypatch) -> None:
    """The HTTP route stays a thin adapter: base64/text encoding preserved,
    service errors mapped to HTTP status."""
    import base64

    from fastapi import HTTPException

    from src.routers import files as files_module

    caller = _caller()
    monkeypatch.setattr(
        "src.routers.files.FileCaller",
        MagicMock(from_context=MagicMock(return_value=caller)),
    )
    monkeypatch.setattr(
        "shared.sdk_files.sdk_read_file",
        AsyncMock(return_value=MagicMock(content=b"hi", binary=False)),
    )
    response = await files_module.read_file(
        files_module.FileReadRequest(path="a.txt", location="reports"),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    assert response.content == "hi"
    assert response.binary is False

    monkeypatch.setattr(
        "shared.sdk_files.sdk_read_file",
        AsyncMock(return_value=MagicMock(content=b"\xff\x00", binary=True)),
    )
    response = await files_module.read_file(
        files_module.FileReadRequest(path="a.bin", location="reports", binary=True),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    assert response.content == base64.b64encode(b"\xff\x00").decode()
    assert response.binary is True

    async def _denied(*args, **kwargs):
        raise FileServiceError(403, {"message": "File policy denied"})

    monkeypatch.setattr("shared.sdk_files.sdk_read_file", _denied)
    with pytest.raises(HTTPException) as exc:
        await files_module.read_file(
            files_module.FileReadRequest(path="a.txt", location="reports"),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        )
    assert exc.value.status_code == 403


# =============================================================================
# Mutation service tests (files.write / files.delete / signed URLs)
# =============================================================================


def _mutation_caller(**kwargs) -> FileCaller:
    """Caller whose DB mock supports the advisory lock + commit probes."""
    caller = _caller(**kwargs)
    caller.db.execute = AsyncMock()
    return caller


def _rw_backend(
    monkeypatch, *, files: dict[tuple[str, str | None], bytes]
) -> tuple[MagicMock, dict[tuple[str, str | None], bytes]]:
    """Fake read/write backend backed by a mutable {(path, scope): content} map."""
    store = dict(files)
    backend = MagicMock()

    async def _read(path, location, scope=None):
        try:
            return store[(path, scope)]
        except KeyError:
            raise FileNotFoundError(f"File not found: {path}")

    async def _write(path, content, location, updated_by="system", scope=None):
        store[(path, scope)] = content

    async def _delete(path, location, scope=None):
        try:
            del store[(path, scope)]
        except KeyError:
            raise FileNotFoundError(f"File not found: {path}")

    async def _exists(path, location, scope=None):
        return (path, scope) in store

    backend.read = AsyncMock(side_effect=_read)
    backend.write = AsyncMock(side_effect=_write)
    backend.delete = AsyncMock(side_effect=_delete)
    backend.exists = AsyncMock(side_effect=_exists)
    monkeypatch.setattr(
        "src.services.file_backend.get_backend", lambda *args: backend
    )
    return backend, store


def _storage(monkeypatch, events: list[str] | None = None) -> MagicMock:
    """Fake FileStorageService; records metadata/presign calls."""
    storage = MagicMock()
    if events is not None:
        storage.record_file_write_metadata = AsyncMock(
            side_effect=lambda **kwargs: events.append("metadata")
        )
    else:
        storage.record_file_write_metadata = AsyncMock()
    storage.generate_presigned_upload_url = AsyncMock(
        return_value="https://s3/put-url"
    )
    storage.generate_presigned_download_url = AsyncMock(
        return_value="https://s3/get-url"
    )
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService", lambda db: storage
    )
    return storage


def _publish(monkeypatch, events: list[str]) -> AsyncMock:
    publish = AsyncMock(side_effect=lambda **kwargs: events.append("publish"))
    monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)
    return publish


def _allow_write_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        "shared.sdk_files.require_file_policy", AsyncMock(return_value=None)
    )


def _deny_all_policies(monkeypatch) -> AsyncMock:
    """Force the real policy gate to deny so the audit path is exercised."""
    monkeypatch.setattr(
        "shared.file_access.authorize_file_policy", AsyncMock(return_value=False)
    )
    return AsyncMock()


class TestSdkWriteFile:
    @pytest.mark.asyncio
    async def test_cloud_write_commits_metadata_before_publishing(
        self, monkeypatch
    ) -> None:
        events: list[str] = []
        caller = _mutation_caller()
        backend, store = _rw_backend(monkeypatch, files={})
        storage = _storage(monkeypatch, events)
        caller.db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
        backend.write = AsyncMock(
            side_effect=lambda *a, **k: events.append("write")
            or store.__setitem__((a[0], k.get("scope")), a[1])
        )
        publish = _publish(monkeypatch, events)
        _allow_write_policy(monkeypatch)

        await sdk_write_file(
            caller,
            path="report.txt",
            content="ready",
            binary=False,
            location="reports",
            scope=None,
        )

        assert events == ["write", "metadata", "commit", "publish"]
        assert store[("report.txt", str(ORG_ID))] == b"ready"
        backend.write.assert_awaited_once()
        assert backend.write.await_args.args[1:4] == (
            b"ready",
            "reports",
            "reader@example.com",
        )
        assert backend.write.await_args.kwargs["scope"] == str(ORG_ID)
        storage.record_file_write_metadata.assert_awaited_once()
        metadata_kwargs = storage.record_file_write_metadata.await_args.kwargs
        assert metadata_kwargs["s3_path"] == resolve_s3_key(
            "reports", str(ORG_ID), "report.txt"
        )
        assert metadata_kwargs["size_bytes"] == 5
        assert metadata_kwargs["sha256"] == hashlib.sha256(b"ready").hexdigest()
        publish.assert_awaited_once_with(
            location="reports", scope=str(ORG_ID), path="report.txt", action="write"
        )

    @pytest.mark.asyncio
    async def test_write_binary_decodes_base64(self, monkeypatch) -> None:
        caller = _mutation_caller()
        backend, store = _rw_backend(monkeypatch, files={})
        _storage(monkeypatch)
        _publish(monkeypatch, [])
        _allow_write_policy(monkeypatch)
        raw = b"\xff\x00\x01binary"

        await sdk_write_file(
            caller,
            path="img.bin",
            content=base64.b64encode(raw).decode(),
            binary=True,
            location="reports",
            scope=None,
        )

        assert store[("img.bin", str(ORG_ID))] == raw

    @pytest.mark.asyncio
    async def test_write_invalid_base64_is_400(self, monkeypatch) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={})
        _storage(monkeypatch)
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_write_file(
                caller,
                path="img.bin",
                content="!!!not-base64!!!",
                binary=True,
                location="reports",
                scope=None,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_write_create_only_with_expected_version_is_400(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={})
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_write_file(
                caller,
                path="a.txt",
                content="hi",
                binary=False,
                location="reports",
                scope=None,
                expected_version="sha256:abc",
                create_only=True,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_write_create_only_existing_is_409(self, monkeypatch) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={("a.txt", str(ORG_ID)): b"old"})
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_write_file(
                caller,
                path="a.txt",
                content="new",
                binary=False,
                location="reports",
                scope=None,
                create_only=True,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "file_exists"
        assert exc.value.detail["current_version"] == (
            "sha256:" + hashlib.sha256(b"old").hexdigest()
        )

    @pytest.mark.asyncio
    async def test_write_expected_version_mismatch_is_409(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={("a.txt", str(ORG_ID)): b"old"})
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_write_file(
                caller,
                path="a.txt",
                content="new",
                binary=False,
                location="reports",
                scope=None,
                expected_version="sha256:stale",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "version_conflict"
        assert exc.value.detail["expected_version"] == "sha256:stale"

    @pytest.mark.asyncio
    async def test_write_expected_version_missing_file_is_409(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={})
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_write_file(
                caller,
                path="gone.txt",
                content="new",
                binary=False,
                location="reports",
                scope=None,
                expected_version="sha256:anything",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "file_missing"

    @pytest.mark.asyncio
    async def test_write_matching_expected_version_succeeds(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller()
        _, store = _rw_backend(monkeypatch, files={("a.txt", str(ORG_ID)): b"old"})
        _storage(monkeypatch)
        _publish(monkeypatch, [])
        _allow_write_policy(monkeypatch)
        version = "sha256:" + hashlib.sha256(b"old").hexdigest()

        await sdk_write_file(
            caller,
            path="a.txt",
            content="new",
            binary=False,
            location="reports",
            scope=None,
            expected_version=version,
        )

        assert store[("a.txt", str(ORG_ID))] == b"new"

    @pytest.mark.asyncio
    async def test_write_denied_is_403_with_audit(self, monkeypatch) -> None:
        _deny_all_policies(monkeypatch)
        emit = AsyncMock()
        monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)
        caller = _mutation_caller()
        backend, _ = _rw_backend(monkeypatch, files={})

        with pytest.raises(FileServiceError) as exc:
            await sdk_write_file(
                caller,
                path="a.txt",
                content="hi",
                binary=False,
                location="reports",
                scope=None,
            )
        assert exc.value.status_code == 403
        assert exc.value.detail["action"] == "write"
        emit.assert_awaited_once()
        caller.db.commit.assert_awaited_once()
        backend.write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_write_local_mode_has_no_cloud_side_effects(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller()
        backend, store = _rw_backend(monkeypatch, files={})
        fss_class = MagicMock()
        monkeypatch.setattr(
            "src.services.file_storage.FileStorageService", fss_class
        )
        publish = AsyncMock()
        monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)
        _allow_write_policy(monkeypatch)

        await sdk_write_file(
            caller,
            path="a.txt",
            content="hi",
            binary=False,
            location="reports",
            scope=None,
            mode="local",
        )

        assert store[("a.txt", str(ORG_ID))] == b"hi"
        backend.write.assert_awaited_once()
        fss_class.assert_not_called()
        publish.assert_not_awaited()
        caller.db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_write_undeclared_solution_location_is_404(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller(solution_id=str(SOLUTION_ID))
        monkeypatch.setattr(
            "src.services.solution_scope.solution_declares_file_location",
            AsyncMock(return_value=False),
        )

        with pytest.raises(FileServiceError) as exc:
            await sdk_write_file(
                caller,
                path="a.txt",
                content="hi",
                binary=False,
                location="reports",
                scope=None,
            )
        assert exc.value.status_code == 404


class TestSdkDeleteFile:
    @pytest.mark.asyncio
    async def test_cloud_delete_commits_metadata_before_publishing(
        self, monkeypatch
    ) -> None:
        from src.services import file_policy_service

        events: list[str] = []
        caller = _mutation_caller()
        backend, store = _rw_backend(
            monkeypatch, files={("report.txt", str(ORG_ID)): b"bye"}
        )
        policy_service = MagicMock()
        policy_service.delete_metadata = AsyncMock(
            side_effect=lambda **kwargs: events.append("metadata")
        )
        monkeypatch.setattr(
            file_policy_service, "FilePolicyService", lambda db: policy_service
        )
        caller.db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
        raw_delete = backend.delete

        async def _delete_and_record(*args, **kwargs):
            events.append("delete")
            await raw_delete(*args, **kwargs)

        backend.delete = AsyncMock(side_effect=_delete_and_record)
        publish = _publish(monkeypatch, events)
        _allow_write_policy(monkeypatch)

        await sdk_delete_file(
            caller, path="report.txt", location="reports", scope=None
        )

        assert events == ["delete", "metadata", "commit", "publish"]
        assert ("report.txt", str(ORG_ID)) not in store
        policy_service.delete_metadata.assert_awaited_once_with(
            organization_id=ORG_ID,
            location="reports",
            path="report.txt",
            solution_id=None,
        )
        publish.assert_awaited_once_with(
            location="reports",
            scope=str(ORG_ID),
            path="report.txt",
            action="delete",
        )

    @pytest.mark.asyncio
    async def test_delete_expected_version_mismatch_is_409(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={("a.txt", str(ORG_ID)): b"old"})
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_delete_file(
                caller,
                path="a.txt",
                location="reports",
                scope=None,
                expected_version="sha256:stale",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "version_conflict"

    @pytest.mark.asyncio
    async def test_delete_expected_version_missing_file_is_409(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={})
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_delete_file(
                caller,
                path="gone.txt",
                location="reports",
                scope=None,
                expected_version="sha256:anything",
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "file_missing"

    @pytest.mark.asyncio
    async def test_delete_missing_file_is_404(self, monkeypatch) -> None:
        caller = _mutation_caller()
        _rw_backend(monkeypatch, files={})
        _allow_write_policy(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_delete_file(
                caller, path="gone.txt", location="reports", scope=None
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_denied_is_403_with_audit(self, monkeypatch) -> None:
        _deny_all_policies(monkeypatch)
        emit = AsyncMock()
        monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)
        caller = _mutation_caller()
        backend, _ = _rw_backend(
            monkeypatch, files={("a.txt", str(ORG_ID)): b"old"}
        )

        with pytest.raises(FileServiceError) as exc:
            await sdk_delete_file(
                caller, path="a.txt", location="reports", scope=None
            )
        assert exc.value.status_code == 403
        assert exc.value.detail["action"] == "delete"
        emit.assert_awaited_once()
        backend.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_local_mode_has_no_cloud_side_effects(
        self, monkeypatch
    ) -> None:
        from src.services import file_policy_service

        caller = _mutation_caller()
        backend, store = _rw_backend(
            monkeypatch, files={("a.txt", str(ORG_ID)): b"old"}
        )
        policy_service = MagicMock()
        policy_service.delete_metadata = AsyncMock()
        monkeypatch.setattr(
            file_policy_service, "FilePolicyService", lambda db: policy_service
        )
        publish = AsyncMock()
        monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)
        _allow_write_policy(monkeypatch)

        await sdk_delete_file(
            caller, path="a.txt", location="reports", scope=None, mode="local"
        )

        assert ("a.txt", str(ORG_ID)) not in store
        backend.delete.assert_awaited_once()
        policy_service.delete_metadata.assert_not_awaited()
        publish.assert_not_awaited()
        caller.db.commit.assert_not_awaited()


class TestSdkSignedUrl:
    @pytest.mark.asyncio
    async def test_signed_get_single_tier_presigns_download(
        self, monkeypatch
    ) -> None:
        _tiers(monkeypatch, [FileTier("org", str(ORG_ID), ORG_ID, None)])
        _allow_write_policy(monkeypatch)
        storage = _storage(monkeypatch)
        caller = _mutation_caller()

        result = await sdk_signed_url(
            caller, path="file.pdf", location="reports", scope=None, method="GET"
        )

        assert result.url == "https://s3/get-url"
        assert result.path == resolve_s3_key("reports", str(ORG_ID), "file.pdf")
        assert result.expires_in == 600
        storage.generate_presigned_download_url.assert_awaited_once_with(
            path=resolve_s3_key("reports", str(ORG_ID), "file.pdf"),
            expires_in=600,
        )
        storage.generate_presigned_upload_url.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_signed_get_multi_tier_prefers_first_existing_permitted(
        self, monkeypatch
    ) -> None:
        _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
        monkeypatch.setattr(
            "shared.sdk_files.authorize_file_policy", AsyncMock(return_value=True)
        )
        backend, _ = _rw_backend(
            monkeypatch, files={("file.pdf", "global"): b"shared"}
        )
        storage = _storage(monkeypatch)

        result = await sdk_signed_url(
            _mutation_caller(),
            path="file.pdf",
            location="reports",
            scope=None,
            method="GET",
        )

        assert [c.kwargs["scope"] for c in backend.exists.await_args_list] == [
            str(SOLUTION_ID),
            str(ORG_ID),
            "global",
        ]
        assert result.path == resolve_s3_key("reports", "global", "file.pdf")
        storage.generate_presigned_download_url.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signed_get_missing_everywhere_presigns_first_allowed(
        self, monkeypatch
    ) -> None:
        _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
        monkeypatch.setattr(
            "shared.sdk_files.authorize_file_policy", AsyncMock(return_value=True)
        )
        _rw_backend(monkeypatch, files={})
        storage = _storage(monkeypatch)

        result = await sdk_signed_url(
            _mutation_caller(),
            path="new.pdf",
            location="reports",
            scope=None,
            method="GET",
        )

        assert result.path == resolve_s3_key(
            "reports", str(SOLUTION_ID), "new.pdf"
        )
        storage.generate_presigned_download_url.assert_awaited_once_with(
            path=resolve_s3_key("reports", str(SOLUTION_ID), "new.pdf"),
            expires_in=600,
        )

    @pytest.mark.asyncio
    async def test_signed_get_skips_denied_tiers(self, monkeypatch) -> None:
        _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
        _rw_backend(
            monkeypatch,
            files={
                ("file.pdf", str(SOLUTION_ID)): b"own",
                ("file.pdf", "global"): b"shared",
            },
        )

        async def _authorize(caller_arg, **kwargs):
            return kwargs.get("scope") != str(SOLUTION_ID)

        monkeypatch.setattr(
            "shared.sdk_files.authorize_file_policy",
            AsyncMock(side_effect=_authorize),
        )
        storage = _storage(monkeypatch)

        result = await sdk_signed_url(
            _mutation_caller(),
            path="file.pdf",
            location="reports",
            scope=None,
            method="GET",
        )

        assert result.path == resolve_s3_key("reports", "global", "file.pdf")
        storage.generate_presigned_download_url.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signed_get_all_denied_is_403_with_audit(
        self, monkeypatch
    ) -> None:
        _tiers(monkeypatch, TIERS_SOLUTION_FIRST)
        monkeypatch.setattr(
            "shared.sdk_files.authorize_file_policy", AsyncMock(return_value=False)
        )
        _rw_backend(monkeypatch, files={("file.pdf", "global"): b"shared"})
        emit = AsyncMock()
        monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)
        caller = _mutation_caller()

        with pytest.raises(FileServiceError) as exc:
            await sdk_signed_url(
                caller, path="file.pdf", location="reports", scope=None, method="GET"
            )
        assert exc.value.status_code == 403
        assert exc.value.detail["action"] == "signed_get"
        emit.assert_awaited_once()
        caller.db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signed_get_undeclared_solution_location_is_404(
        self, monkeypatch
    ) -> None:
        caller = _mutation_caller(solution_id=str(SOLUTION_ID))
        monkeypatch.setattr(
            "src.services.solution_scope.solution_declares_file_location",
            AsyncMock(return_value=False),
        )

        with pytest.raises(FileServiceError) as exc:
            await sdk_signed_url(
                caller, path="a.txt", location="reports", scope=None, method="GET"
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_signed_put_presigns_upload_without_metadata_mutation(
        self, monkeypatch
    ) -> None:
        _allow_write_policy(monkeypatch)
        storage = _storage(monkeypatch)
        caller = _mutation_caller()

        result = await sdk_signed_url(
            caller,
            path="file.pdf",
            location="uploads",
            scope=None,
            method="PUT",
            content_type="application/pdf",
            expires_in=3600,
        )

        assert result.url == "https://s3/put-url"
        assert result.path == resolve_s3_key("uploads", str(ORG_ID), "file.pdf")
        assert result.expires_in == 3600
        storage.generate_presigned_upload_url.assert_awaited_once_with(
            path=resolve_s3_key("uploads", str(ORG_ID), "file.pdf"),
            content_type="application/pdf",
            expires_in=3600,
        )
        storage.record_file_write_metadata.assert_not_awaited()
        storage.generate_presigned_download_url.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_signed_put_denied_is_403_with_audit(self, monkeypatch) -> None:
        _deny_all_policies(monkeypatch)
        emit = AsyncMock()
        monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)
        caller = _mutation_caller()

        with pytest.raises(FileServiceError) as exc:
            await sdk_signed_url(
                caller,
                path="file.pdf",
                location="uploads",
                scope=None,
                method="PUT",
            )
        assert exc.value.status_code == 403
        assert exc.value.detail["action"] == "signed_put"
        emit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signed_put_invalid_scope_propagates_value_error(
        self, monkeypatch
    ) -> None:
        """An invalid superuser scope historically reaches the 422 middleware
        (the PUT handler never caught it); the service preserves that."""
        caller = _mutation_caller(
            user=_user(is_superuser=True, organization_id=None), org_id=None
        )
        _allow_write_policy(monkeypatch)

        with pytest.raises(ValueError, match="Invalid scope value"):
            await sdk_signed_url(
                caller,
                path="file.pdf",
                location="uploads",
                scope="not-a-uuid",
                method="PUT",
            )

    @pytest.mark.asyncio
    async def test_signed_url_rejects_path_traversal_with_400(
        self, monkeypatch
    ) -> None:
        _allow_write_policy(monkeypatch)
        _storage(monkeypatch)

        with pytest.raises(FileServiceError) as exc:
            await sdk_signed_url(
                _mutation_caller(),
                path="../etc/passwd",
                location="uploads",
                scope=str(ORG_ID),
                method="PUT",
            )
        assert exc.value.status_code == 400
