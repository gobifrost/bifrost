"""Editor validators describe final bytes and reject stale writes across routes."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.models import FileContentRequest, UpdatePropertiesRequest
from src.routers import decorator_properties, files


SOURCE = b'from bifrost import workflow\n\n@workflow(name="Original")\nasync def example():\n    return 1\n'


@pytest.fixture
def editor(monkeypatch):
    storage = MagicMock()
    storage.read_file = AsyncMock(return_value=(SOURCE, None))
    storage.write_file = AsyncMock()
    monkeypatch.setattr("src.services.file_storage.FileStorageService", lambda _db: storage)
    monkeypatch.setattr(decorator_properties, "FileStorageService", lambda _db: storage)
    monkeypatch.setattr(files, "authorize_operation", AsyncMock())
    monkeypatch.setattr(files, "_lock_file_mutation", AsyncMock())
    monkeypatch.setattr(decorator_properties, "load_caller", AsyncMock())
    monkeypatch.setattr(decorator_properties, "require_operation", lambda *_: None)
    return storage, MagicMock(), SimpleNamespace(email="admin@example.com"), AsyncMock()


@pytest.mark.asyncio
async def test_editor_read_hashes_raw_bytes(editor):
    storage, ctx, user, db = editor
    storage.read_file.return_value = (b"\xff\x00", None)
    response = await files.get_file_content_editor(ctx, user, "binary.bin", db)
    assert response.encoding == "base64"
    assert response.etag == hashlib.sha256(b"\xff\x00").hexdigest()


@pytest.mark.asyncio
async def test_save_accepts_read_validator_and_hashes_final_content(editor):
    storage, ctx, user, db = editor
    read = await files.get_file_content_editor(ctx, user, "example.py", db)
    final = SOURCE.replace(b'@workflow(', b'@workflow(id="injected", ')
    storage.write_file.return_value = SimpleNamespace(
        final_content=final, content_modified=True, pending_deactivations=[],
        workflow_id_conflicts=[], diagnostics=[], needs_indexing=False,
    )
    response = await files.put_file_content_editor(
        FileContentRequest(path="example.py", content=SOURCE.decode(), expected_etag=read.etag),
        ctx, user, db,
    )
    storage.write_file.assert_awaited_once()
    assert response.content == final.decode()
    assert response.etag == hashlib.sha256(final).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["file", "decorator"])
@pytest.mark.parametrize("validator", ["stale", '5f214a24fb89bfcf1f43e12b36cfc74e'])
async def test_stale_or_pre_upgrade_validator_never_writes(editor, route, validator):
    storage, ctx, user, db = editor
    with pytest.raises(HTTPException) as error:
        if route == "file":
            await files.put_file_content_editor(
                FileContentRequest(path="example.py", content="changed", expected_etag=validator),
                ctx, user, db,
            )
        else:
            await decorator_properties.update_decorator_properties(
                ctx, user, UpdatePropertiesRequest(
                    path="example.py", function_name="example", properties={"name": "Changed"},
                    expected_etag=validator,
                ), db,
            )
    assert error.value.status_code == 409
    assert error.value.detail["reason"] == "content_changed"
    storage.write_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("modified", [False, True])
async def test_decorator_accepts_editor_validator_and_returns_final_hash(editor, modified, monkeypatch):
    storage, ctx, user, db = editor
    read = await files.get_file_content_editor(ctx, user, "example.py", db)
    final = SOURCE.replace(b'"Original"', b'"Changed"') + b"# storage normalization\n"
    # Parsing is covered by test_decorator_property_service; this route contract
    # must not depend on the LibCST module identities that its fixtures replace.
    service = SimpleNamespace(write_properties=MagicMock(return_value=SimpleNamespace(
        modified=modified, new_content=SOURCE.replace(b'"Original"', b'"Changed"').decode(), changes=[],
    )))
    monkeypatch.setattr(decorator_properties, "DecoratorPropertyService", lambda: service)
    storage.write_file.return_value = SimpleNamespace(final_content=final)
    response = await decorator_properties.update_decorator_properties(
        ctx, user, UpdatePropertiesRequest(
            path="example.py", function_name="example",
            properties={"name": "Changed"} if modified else {}, expected_etag=read.etag,
        ), db,
    )
    service.write_properties.assert_called_once_with(
        SOURCE.decode(), "example", {"name": "Changed"} if modified else {},
    )
    assert response.modified is modified
    assert response.new_etag == hashlib.sha256(final if modified else SOURCE).hexdigest()
    if modified:
        storage.write_file.assert_awaited_once()
    else:
        storage.write_file.assert_not_awaited()
