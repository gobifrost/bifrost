"""Regression tests for externally influenced values at logging boundaries."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _assert_sanitized(message: str) -> None:
    assert "\n" not in message
    assert "\r" not in message
    assert "\x1b" not in message


@pytest.mark.asyncio
async def test_module_cache_sanitizes_path_in_log(monkeypatch, caplog):
    from src.core import module_cache

    redis_connection = MagicMock()
    redis_connection.sadd = AsyncMock(return_value=1)
    redis = MagicMock()
    redis.setex = AsyncMock()
    redis._get_redis = AsyncMock(return_value=redis_connection)
    monkeypatch.setattr(module_cache, "get_redis_client", lambda: redis)

    path = "workflows/example.py\nFORGED\x1b[31m"
    with caplog.at_level(logging.DEBUG, logger=module_cache.__name__):
        await module_cache.set_module(path, "content", "hash")

    message = caplog.messages[-1]
    _assert_sanitized(message)
    assert "example.py\\nFORGED" in message


@pytest.mark.asyncio
async def test_deactivation_sanitizes_invalid_workflow_id(caplog):
    from src.services.file_storage import deactivation

    service = deactivation.DeactivationProtectionService(AsyncMock())
    workflow_id = "invalid\nFORGED\x1b[31m"

    with caplog.at_level(logging.WARNING, logger=deactivation.__name__):
        await service.apply_workflow_replacements({workflow_id: "replacement"})

    message = caplog.messages[-1]
    _assert_sanitized(message)
    assert "invalid\\nFORGED" in message


@pytest.mark.asyncio
async def test_diagnostics_sanitizes_file_path(monkeypatch, caplog):
    from src.services.file_storage import diagnostics

    notification_service = MagicMock()
    notification_service.find_admin_notification_by_title = AsyncMock(
        return_value=SimpleNamespace(id="notification-id")
    )
    notification_service.dismiss_notification = AsyncMock()
    monkeypatch.setattr(
        diagnostics,
        "get_notification_service",
        lambda: notification_service,
    )

    path = "workflows/example.py\nFORGED\x1b[31m"
    service = diagnostics.DiagnosticsService(AsyncMock())
    with caplog.at_level(logging.INFO, logger=diagnostics.__name__):
        await service.clear_diagnostic_notification(path)

    message = caplog.messages[-1]
    _assert_sanitized(message)
    assert "example.py\\nFORGED" in message


@pytest.mark.asyncio
async def test_file_ops_sanitizes_deleted_path(monkeypatch, caplog):
    from src.core import pubsub
    from src.services.file_storage import file_ops

    service = file_ops.FileOperationsService(
        db=AsyncMock(),
        settings=SimpleNamespace(s3_bucket="test"),
        s3_client=MagicMock(),
        diagnostics=MagicMock(),
        deactivation=MagicMock(),
        file_hash_fn=lambda content: "hash",
        content_type_fn=lambda path: "text/plain",
        platform_entity_detector_fn=lambda path, content: None,
        extract_metadata_fn=AsyncMock(),
        remove_metadata_fn=AsyncMock(),
    )
    monkeypatch.setattr(service, "_delete_from_s3", AsyncMock())
    monkeypatch.setattr(service, "_remove_from_search_index", AsyncMock())
    monkeypatch.setattr(service, "_handle_app_file_cleanup", AsyncMock())
    monkeypatch.setattr(service, "_invalidate_module_cache_if_python", AsyncMock())
    monkeypatch.setattr(pubsub, "publish_file_activity", AsyncMock())

    path = "workflows/example.py\nFORGED\x1b[31m"
    with caplog.at_level(logging.INFO, logger=file_ops.__name__):
        await service.delete_file(path)

    message = caplog.messages[-1]
    _assert_sanitized(message)
    assert "example.py\\nFORGED" in message


@pytest.mark.asyncio
async def test_file_storage_sanitizes_syntax_error_path(caplog):
    from src.services.file_storage import service as file_storage

    storage = object.__new__(file_storage.FileStorageService)
    path = "workflows/example.py\nFORGED\x1b[31m"

    with caplog.at_level(logging.WARNING, logger=file_storage.__name__):
        await storage._index_python_file_full(path, b"def broken(")

    message = caplog.messages[-1]
    _assert_sanitized(message)
    assert "example.py\\nFORGED" in message


@pytest.mark.asyncio
async def test_sdk_scanner_sanitizes_reference_and_path(caplog):
    from src.services import sdk_reference_scanner

    scanner = sdk_reference_scanner.SDKReferenceScanner(AsyncMock())
    scanner.get_all_config_keys = AsyncMock(return_value=set())
    key = "danger\x1b[31m"
    path = "workflows/example.py\nFORGED"

    with caplog.at_level(logging.DEBUG, logger=sdk_reference_scanner.__name__):
        await scanner.scan_file(path, f'config.get("{key}")')

    for message in caplog.messages:
        _assert_sanitized(message)
    assert any("example.py\\nFORGED" in message for message in caplog.messages)
    assert any("danger" in message for message in caplog.messages)
