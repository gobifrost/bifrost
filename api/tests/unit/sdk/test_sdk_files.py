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
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from shared.file_access import FileCaller, FileServiceError
from shared.sdk_files import (
    sdk_file_exists,
    sdk_file_stat,
    sdk_list_files,
    sdk_read_file,
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
