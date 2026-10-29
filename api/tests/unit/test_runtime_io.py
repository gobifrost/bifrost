"""Runtime transport is confined to its authenticated run or attempt."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from shared.models import RuntimeLogRequest, ServiceControlRequest
from shared.runtime_io import service_control, write_runtime_log


@pytest.mark.asyncio
async def test_log_destination_is_the_authenticated_run(monkeypatch):
    from bifrost import _logging

    run_id = uuid4()
    principal = SimpleNamespace(runtime_authority=SimpleNamespace(kind="workflow_execution", credential_id=run_id))
    append = AsyncMock(return_value="1-0")
    publish = AsyncMock()
    monkeypatch.setattr(_logging, "append_log_to_stream_async", append)
    monkeypatch.setattr(_logging, "publish_log_to_pubsub_async", publish)

    result = await write_runtime_log(principal, RuntimeLogRequest(level="INFO", message="Contoso check complete"))

    assert result.entry_id == "1-0"
    assert append.call_args.args[0] == run_id
    assert publish.call_args.args[0] == run_id
    with pytest.raises(ValidationError):
        RuntimeLogRequest.model_validate({"level": "INFO", "message": "line", "execution_id": str(uuid4())})


@pytest.mark.asyncio
async def test_human_credentials_cannot_use_runtime_log_transport():
    with pytest.raises(HTTPException) as error:
        await write_runtime_log(SimpleNamespace(runtime_authority=None), RuntimeLogRequest(level="INFO", message="line"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_service_control_reads_only_its_attempt_and_parent_rotated_token(monkeypatch):
    import json

    from src.core import cache
    from src.core.cache.keys import service_ready_key, service_stop_key, service_token_key

    attempt_id = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    rotated_expiry = expires_at + timedelta(minutes=5)
    redis = SimpleNamespace(
        setex=AsyncMock(),
        get=AsyncMock(side_effect=[None, json.dumps({"token": "contoso-attempt-token", "expires_at": rotated_expiry.isoformat()})]),
    )

    @asynccontextmanager
    async def connection():
        yield redis

    monkeypatch.setattr(cache, "get_redis", connection)
    principal = SimpleNamespace(runtime_authority=SimpleNamespace(kind="service_attempt", credential_id=attempt_id))

    result = await service_control(principal, ServiceControlRequest(ready=True, token_expires_at=expires_at))

    redis.setex.assert_awaited_once_with(service_ready_key(str(attempt_id)), 120, "1")
    assert [call.args[0] for call in redis.get.call_args_list] == [
        service_stop_key(str(attempt_id)), service_token_key(str(attempt_id)),
    ]
    assert result.access_token == "contoso-attempt-token"
    assert result.token_expires_at == rotated_expiry
    assert not result.stop_requested


def test_child_log_write_uses_the_shared_transport(monkeypatch):
    from bifrost import _runtime_io

    response = Mock(status_code=200)
    response.json.return_value = {"entry_id": "12-0"}
    client = SimpleNamespace(engine_request_sync=Mock(return_value=response))
    monkeypatch.setattr(_runtime_io, "get_client", lambda: client)

    entry_id = _runtime_io.write_log("INFO", "Contoso ready")

    assert entry_id == "12-0"
    client.engine_request_sync.assert_called_once_with(
        "POST", "/api/sdk/runtime/logs", json={"level": "INFO", "message": "Contoso ready"},
    )
