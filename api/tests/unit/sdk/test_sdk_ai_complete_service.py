"""Focused unit tests for the shared SDK AI operation service.

Covers ``shared.sdk_ai.complete_sdk_ai`` / ``get_sdk_model_info`` and
the thin HTTP adapters in ``api/src/routers/cli.py``:

- scope rule reuse (UNSET/global/UUID, 403/422 precedence, best-effort
  usage attribution never fails the request);
- file-input decoding and the user-message requirement;
- response shaping and profile/model/max-tokens forwarding;
- error mapping (401 provider auth, 503 ValueError, sanitized 500);
- usage-recording failures never fail the request;
- the DB connection release boundary (session released after profile
  lookup, before provider latency).
"""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from shared.sdk_ai import SdkAIError


def _principal(**kwargs):
    return SimpleNamespace(
        user_id=kwargs.get("user_id", uuid4()),
        organization_id=kwargs.get("organization_id", uuid4()),
        is_superuser=kwargs.get("is_superuser", False),
        email=kwargs.get("email", "ai-test@example.com"),
    )


def _llm_response(**kwargs):
    return SimpleNamespace(
        content=kwargs.get("content", "Hello"),
        input_tokens=kwargs.get("input_tokens", 3),
        output_tokens=kwargs.get("output_tokens", 5),
        cache_read_tokens=kwargs.get("cache_read_tokens", 0),
        cache_write_tokens=kwargs.get("cache_write_tokens", 0),
        provider_cost=kwargs.get("provider_cost", None),
        model=kwargs.get("model", "gpt-4o"),
    )


def _client(response=None):
    client = AsyncMock()
    client.provider_name = "openai"
    client.model_name = "gpt-4o"
    client.complete.return_value = response or _llm_response()
    return client


@pytest.mark.asyncio
async def test_complete_forwards_profile_model_max_tokens():
    from shared import sdk_ai

    client = _client()
    session = AsyncMock()
    principal = _principal()

    with (
        patch("src.services.llm.get_llm_client", new=AsyncMock(return_value=client)) as get_client,
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=AsyncMock()) as record,
        patch(
            "shared.sdk_config.resolve_sdk_scope",
            new=AsyncMock(return_value=principal.organization_id),
        ),
    ):
        # The service imports get_llm_client at call time from the
        # src.services.llm package (re-exported from .factory); point
        # that attribute at the mock so both import paths resolve.
        import src.services.llm as llm_pkg

        llm_pkg.get_llm_client = get_client
        result = await sdk_ai.complete_sdk_ai(
            session,
            principal,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=42,
            model="gpt-4o",
            profile="Reasoning",
            execution_id=None,
            scope=None,
            input_files=[],
        )

    assert result["content"] == "Hello"
    assert result["input_tokens"] == 3
    assert result["output_tokens"] == 5
    assert result["model"] == "gpt-4o"
    get_client.assert_awaited_once_with(session, profile_name="Reasoning")
    client.complete.assert_awaited_once()
    kwargs = client.complete.await_args.kwargs
    assert kwargs["max_tokens"] == 42
    assert kwargs["model"] == "gpt-4o"
    assert record.await_count == 1


@pytest.mark.asyncio
async def test_complete_releases_connection_before_provider():
    from shared import sdk_ai

    order: list[str] = []

    async def _close():
        order.append("close")

    async def _complete(**kwargs):
        order.append("complete")
        return _llm_response()

    client = AsyncMock()
    client.provider_name = "openai"
    client.model_name = "gpt-4o"
    client.complete.side_effect = _complete
    session = AsyncMock()
    session.close.side_effect = _close
    principal = _principal()

    import src.services.llm as llm_pkg

    get_client = AsyncMock(return_value=client)
    llm_pkg.get_llm_client = get_client
    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=AsyncMock()),
        patch(
            "shared.sdk_config.resolve_sdk_scope",
            new=AsyncMock(return_value=principal.organization_id),
        ),
    ):
        await sdk_ai.complete_sdk_ai(
            session,
            principal,
            messages=[{"role": "user", "content": "Hi"}],
        )

    assert order == ["close", "complete"]
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_input_files_attach_to_last_user_message():
    from shared import sdk_ai

    client = _client()
    session = AsyncMock()
    principal = _principal()
    payload = base64.b64encode(b"%PDF-data").decode()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock(return_value=client)
    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=AsyncMock()),
        patch(
            "shared.sdk_config.resolve_sdk_scope",
            new=AsyncMock(return_value=principal.organization_id),
        ),
    ):
        await sdk_ai.complete_sdk_ai(
            session,
            principal,
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "summarize this"},
            ],
            input_files=[
                SimpleNamespace(
                    filename="doc.pdf",
                    content_type="application/pdf",
                    data_base64=payload,
                )
            ],
        )

    sent_messages = client.complete.await_args.kwargs["messages"]
    assert sent_messages[-1].role == "user"
    assert len(sent_messages[-1].input_files) == 1
    assert sent_messages[-1].input_files[0].data == b"%PDF-data"
    assert sent_messages[0].input_files == []


@pytest.mark.asyncio
async def test_complete_input_files_require_user_message():
    from shared import sdk_ai

    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock()
    with pytest.raises(SdkAIError) as exc_info:
        await sdk_ai.complete_sdk_ai(
            session,
            principal,
            messages=[{"role": "system", "content": "sys"}],
            input_files=[
                SimpleNamespace(
                    filename="a.txt",
                    content_type="text/plain",
                    data_base64=base64.b64encode(b"x").decode(),
                )
            ],
        )

    assert exc_info.value.status_code == 503
    assert "user message" in exc_info.value.detail


@pytest.mark.asyncio
async def test_complete_bad_base64_is_503():
    from shared import sdk_ai

    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock()
    with pytest.raises(SdkAIError) as exc_info:
        await sdk_ai.complete_sdk_ai(
            session,
            principal,
            messages=[{"role": "user", "content": "hi"}],
            input_files=[
                SimpleNamespace(
                    filename="a.txt",
                    content_type="text/plain",
                    data_base64="!!!not-base64!!!",
                )
            ],
        )

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_complete_value_error_from_client_is_503():
    from shared import sdk_ai

    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock(side_effect=ValueError("no profile"))
    with pytest.raises(SdkAIError) as exc_info:
        await sdk_ai.complete_sdk_ai(
            session, principal, messages=[{"role": "user", "content": "hi"}]
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "no profile"


@pytest.mark.asyncio
async def test_complete_provider_auth_is_401():
    from shared import sdk_ai

    class AuthenticationError(Exception):
        pass

    AuthenticationError.__module__ = "anthropic"

    client = AsyncMock()
    client.complete.side_effect = AuthenticationError("bad key")
    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock(return_value=client)
    with pytest.raises(SdkAIError) as exc_info:
        await sdk_ai.complete_sdk_ai(
            session, principal, messages=[{"role": "user", "content": "hi"}]
        )

    assert exc_info.value.status_code == 401
    assert "Anthropic" in exc_info.value.detail


@pytest.mark.asyncio
async def test_complete_generic_error_is_sanitized_500():
    from shared import sdk_ai

    client = AsyncMock()
    client.complete.side_effect = RuntimeError("secret boom")
    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock(return_value=client)
    with pytest.raises(SdkAIError) as exc_info:
        await sdk_ai.complete_sdk_ai(
            session, principal, messages=[{"role": "user", "content": "hi"}]
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "AI completion failed. See server logs for details."
    assert "boom" not in exc_info.value.detail


@pytest.mark.asyncio
async def test_complete_usage_scope_422_never_fails_request():
    from shared import sdk_ai
    from shared.sdk_config import ScopeResolutionError

    client = _client()
    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock(return_value=client)
    record = AsyncMock()
    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=record),
        patch(
            "shared.sdk_config.resolve_sdk_scope",
            new=AsyncMock(side_effect=ScopeResolutionError(422, "bad scope")),
        ),
    ):
        result = await sdk_ai.complete_sdk_ai(
            session,
            principal,
            messages=[{"role": "user", "content": "hi"}],
            scope="not-a-uuid",
        )

    assert result["content"] == "Hello"
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_complete_usage_redis_failure_never_fails_request():
    from shared import sdk_ai

    client = _client()
    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock(return_value=client)
    record = AsyncMock()
    with (
        patch(
            "src.core.cache.get_shared_redis",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ),
        patch("src.services.ai_usage_service.record_ai_usage", new=record),
    ):
        result = await sdk_ai.complete_sdk_ai(
            session, principal, messages=[{"role": "user", "content": "hi"}]
        )

    assert result["content"] == "Hello"
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_complete_malformed_execution_id_never_fails_request():
    from shared import sdk_ai

    client = _client()
    session = AsyncMock()
    principal = _principal()

    import src.services.llm as llm_pkg

    llm_pkg.get_llm_client = AsyncMock(return_value=client)
    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch(
            "shared.sdk_config.resolve_sdk_scope",
            new=AsyncMock(return_value=principal.organization_id),
        ),
        patch("src.services.ai_usage_service.record_ai_usage", new=AsyncMock()),
    ):
        result = await sdk_ai.complete_sdk_ai(
            session,
            principal,
            messages=[{"role": "user", "content": "hi"}],
            execution_id="not-a-uuid",
        )

    assert result["content"] == "Hello"


@pytest.mark.asyncio
async def test_resolve_sdk_org_id_preserves_422_and_403():
    from fastapi import HTTPException

    from src.routers.cli import _resolve_sdk_org_id

    org_id = uuid4()

    # 422 for a malformed scope.
    caller = SimpleNamespace(organization_id=org_id, is_superuser=False)
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_sdk_org_id(caller, "not-a-uuid", AsyncMock())
    assert exc_info.value.status_code == 422

    # 403 for a cross-org scope without bypass.
    other = uuid4()
    no_provider_db = AsyncMock()
    no_provider_db.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: None
    )
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_sdk_org_id(caller, str(other), no_provider_db)
    assert exc_info.value.status_code == 403

    # Own org resolves without any DB lookup.
    resolved = await _resolve_sdk_org_id(caller, str(org_id), AsyncMock())
    assert resolved == str(org_id)

    # UNSET resolves to the caller's own org.
    resolved = await _resolve_sdk_org_id(caller, None, AsyncMock())
    assert resolved == str(org_id)


@pytest.mark.asyncio
async def test_get_model_info_returns_provider_and_model():
    from shared import sdk_ai

    session = AsyncMock()
    principal = _principal()
    config = SimpleNamespace(provider="openai", model="gpt-4o")

    with patch(
        "src.services.llm.factory.get_llm_config",
        new=AsyncMock(return_value=config),
    ):
        result = await sdk_ai.get_sdk_model_info(session, principal)

    assert result == {"provider": "openai", "model": "gpt-4o"}


@pytest.mark.asyncio
async def test_get_model_info_missing_config_is_404():
    from shared import sdk_ai

    session = AsyncMock()
    principal = _principal()

    with patch(
        "src.services.llm.factory.get_llm_config",
        new=AsyncMock(side_effect=ValueError("no AI config")),
    ):
        with pytest.raises(SdkAIError) as exc_info:
            await sdk_ai.get_sdk_model_info(session, principal)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "no AI config"


@pytest.mark.asyncio
async def test_http_complete_maps_service_error():
    from fastapi import HTTPException

    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_complete

    with patch(
        "shared.sdk_ai.complete_sdk_ai",
        new=AsyncMock(side_effect=SdkAIError(401, "bad key")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await cli_ai_complete(
                CLIAICompleteRequest(messages=[{"role": "user", "content": "hi"}]),
                _principal(),
                AsyncMock(),
            )

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_http_info_maps_service_error():
    from fastapi import HTTPException

    from src.routers.cli import cli_ai_info

    with patch(
        "shared.sdk_ai.get_sdk_model_info",
        new=AsyncMock(side_effect=SdkAIError(404, "missing")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await cli_ai_info(_principal(), AsyncMock())

    assert exc_info.value.status_code == 404
