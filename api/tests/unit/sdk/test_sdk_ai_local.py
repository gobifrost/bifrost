"""Engine-local transport for the fixed ``ai`` unary calls.

Covers ``ai.complete`` and ``ai.model_info`` without a forked child:

- both ops ride the async SDK channel (allowlist; import channel rejects);
- the parent dispatcher calls the same ``shared.sdk_ai`` service the HTTP
  handlers call, with the same ``CLIAICompleteRequest`` DTO validation;
- the token-equivalent caller comes from ``_table_user_for_principal``;
- usage ``execution_id`` comes from the parent principal even when the
  child frame supplies another one; the requested ``org_id`` rides as an
  untrusted best-effort scope (never pre-resolved here);
- the dispatcher commits after the shared completion (usage is flush-only)
  and maps ``SdkAIError`` through its own status;
- the SDK facade keeps knowledge composition, structured-output handling,
  and input-file encoding on the child, sends composed messages plus
  encoded files, derives child/parent deadlines from the requested
  timeout, maps local status errors to the public ``RuntimeError`` text,
  and never falls back to HTTP.
"""

from __future__ import annotations

import base64
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._local_transport import OP_AI_COMPLETE, OP_AI_MODEL_INFO


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Exercise real flushes while rolling back seeded rows after each test."""
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(**kwargs):
    data = {
        "organization": kwargs.get("organization"),
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", str(uuid4())),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = kwargs["solution_id"]
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _workflow_principal(**kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(**kwargs))


def _frame(op, frame_id=None, **fields):
    return {"v": 1, "id": frame_id or f"ai-{uuid4().hex}", "op": op, **fields}


async def _dispatch(db_session, principal, frame):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


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


def _fake_client(response=None):
    client = AsyncMock()
    client.provider_name = "openai"
    client.model_name = "gpt-4o"
    client.complete.return_value = response or _llm_response()
    return client


def _patch_provider(client):
    """Patch the provider factory plus usage plumbing with a fake client."""
    import src.services.llm as llm_pkg

    return (
        patch.object(llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)),
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=AsyncMock()),
    )


# =============================================================================
# Allowlist
# =============================================================================


def test_ai_ops_on_sdk_allowlist_only():
    from src.services.execution.sdk_local_dispatch import (
        IMPORT_CHANNEL_ALLOWED_OPS,
        SDK_CHANNEL_ALLOWED_OPS,
    )

    assert OP_AI_COMPLETE in SDK_CHANNEL_ALLOWED_OPS
    assert OP_AI_MODEL_INFO in SDK_CHANNEL_ALLOWED_OPS
    assert OP_AI_COMPLETE not in IMPORT_CHANNEL_ALLOWED_OPS
    assert OP_AI_MODEL_INFO not in IMPORT_CHANNEL_ALLOWED_OPS


@pytest.mark.asyncio
async def test_unknown_ai_op_rejected(db_session):
    denied = await _dispatch(
        db_session, _workflow_principal(), _frame("ai.stream", messages=[])
    )
    assert denied["ok"] is False
    assert denied["status"] == 404


# =============================================================================
# ai.complete success + DTO parity
# =============================================================================


@pytest.mark.asyncio
async def test_complete_success_parity_with_http(db_session):
    """Local dispatch returns the same shaped response as the HTTP handler."""
    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_complete

    org_id = uuid4()
    principal = _workflow_principal(
        organization={"id": str(org_id)}, execution_id=str(uuid4())
    )
    client = _fake_client()
    patches = _patch_provider(client)
    frame = _frame(
        OP_AI_COMPLETE,
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=42,
        org_id=None,
        profile="Reasoning",
        model="gpt-4o",
        execution_id=str(uuid4()),
        input_files=[],
    )
    with patches[0], patches[1], patches[2]:
        local = await _dispatch(db_session, principal, frame)
        http_resp = await cli_ai_complete(
            CLIAICompleteRequest(
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=42,
                profile="Reasoning",
                model="gpt-4o",
            ),
            SimpleNamespace(
                user_id=uuid4(), organization_id=org_id, is_superuser=False
            ),
            AsyncMock(),
        )
    assert local["ok"] is True, local
    assert local["result"]["content"] == "Hello"
    assert local["result"]["model"] == "gpt-4o"
    assert local["result"]["input_tokens"] == 3
    assert local["result"]["output_tokens"] == 5
    assert http_resp.content == local["result"]["content"]
    assert http_resp.model == local["result"]["model"]


@pytest.mark.asyncio
async def test_complete_forwards_profile_model_max_tokens(db_session):
    principal = _workflow_principal(execution_id=str(uuid4()))
    client = _fake_client()
    patches = _patch_provider(client)
    frame = _frame(
        OP_AI_COMPLETE,
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=77,
        org_id=None,
        profile="Reasoning",
        model="gpt-4o",
        execution_id=None,
        input_files=[],
    )
    with patches[0], patches[1], patches[2]:
        resp = await _dispatch(db_session, principal, frame)
    assert resp["ok"] is True, resp
    client.complete.assert_awaited_once()
    kwargs = client.complete.await_args.kwargs
    assert kwargs["max_tokens"] == 77
    assert kwargs["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_complete_missing_messages_is_422(db_session):
    principal = _workflow_principal(execution_id=str(uuid4()))
    resp = await _dispatch(
        db_session,
        principal,
        _frame(OP_AI_COMPLETE, max_tokens=None, input_files=[]),
    )
    assert resp["ok"] is False
    assert resp["status"] == 422


@pytest.mark.asyncio
async def test_complete_bad_timeout_is_422(db_session):
    principal = _workflow_principal(execution_id=str(uuid4()))
    for bad in ("soon", -5, 0, True):
        resp = await _dispatch(
            db_session,
            principal,
            _frame(
                OP_AI_COMPLETE,
                messages=[{"role": "user", "content": "Hi"}],
                input_files=[],
                timeout=bad,
            ),
        )
        assert resp["ok"] is False, bad
        assert resp["status"] == 422, bad


@pytest.mark.asyncio
async def test_complete_input_files_decode_on_parent(db_session):
    principal = _workflow_principal(execution_id=str(uuid4()))
    client = _fake_client()
    patches = _patch_provider(client)
    payload = base64.b64encode(b"%PDF-data").decode()
    frame = _frame(
        OP_AI_COMPLETE,
        messages=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "summarize this"},
        ],
        input_files=[
            {
                "filename": "doc.pdf",
                "content_type": "application/pdf",
                "data_base64": payload,
            }
        ],
    )
    with patches[0], patches[1], patches[2]:
        resp = await _dispatch(db_session, principal, frame)
    assert resp["ok"] is True, resp
    sent = client.complete.await_args.kwargs["messages"]
    assert sent[-1].role == "user"
    assert len(sent[-1].input_files) == 1
    assert sent[-1].input_files[0].data == b"%PDF-data"


@pytest.mark.asyncio
async def test_complete_files_require_user_message_maps_503(db_session):
    principal = _workflow_principal(execution_id=str(uuid4()))
    resp = await _dispatch(
        db_session,
        principal,
        _frame(
            OP_AI_COMPLETE,
            messages=[{"role": "system", "content": "sys"}],
            input_files=[
                {
                    "filename": "a.txt",
                    "content_type": "text/plain",
                    "data_base64": base64.b64encode(b"x").decode(),
                }
            ],
        ),
    )
    assert resp["ok"] is False
    assert resp["status"] == 503
    assert "user message" in resp["detail"]


# =============================================================================
# Scope best-effort + usage attribution + commit
# =============================================================================


@pytest.mark.asyncio
async def test_complete_invalid_scope_still_returns_provider_response(db_session):
    """An invalid usage scope never fails the completion (HTTP parity)."""
    principal = _workflow_principal(execution_id=str(uuid4()))
    client = _fake_client()
    patches = _patch_provider(client)
    record = patches[2].new
    frame = _frame(
        OP_AI_COMPLETE,
        messages=[{"role": "user", "content": "Hi"}],
        org_id="not-a-uuid",
        input_files=[],
    )
    with patches[0], patches[1], patches[2]:
        resp = await _dispatch(db_session, principal, frame)
    assert resp["ok"] is True, resp
    assert resp["result"]["content"] == "Hello"
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_complete_failed_usage_flush_still_returns_provider_response(db_session):
    """A failed usage write must not poison the final HTTP/local commit."""
    import src.services.llm as llm_pkg

    async def fail_usage(*, session, **_kwargs):
        await session.execute(text("SELECT 1 / 0"))

    with (
        patch.object(
            llm_pkg, "get_llm_client", new=AsyncMock(return_value=_fake_client())
        ),
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=fail_usage),
    ):
        resp = await _dispatch(
            db_session,
            _workflow_principal(execution_id=str(uuid4())),
            _frame(
                OP_AI_COMPLETE,
                messages=[{"role": "user", "content": "Hi"}],
                input_files=[],
            ),
        )
    assert resp["ok"] is True, resp
    assert resp["result"]["content"] == "Hello"
    assert (await db_session.execute(text("SELECT 1"))).scalar_one() == 1


@pytest.mark.asyncio
async def test_complete_usage_uses_parent_principal_not_child_claims(db_session):
    """Actor and execution_id come from the dispatch principal only."""
    from src.core.constants import SYSTEM_USER_UUID

    org_id = uuid4()
    parent_exec = str(uuid4())
    principal = _workflow_principal(
        organization={"id": str(org_id)}, execution_id=parent_exec
    )
    client = _fake_client()
    patches = _patch_provider(client)
    record = patches[2].new
    frame = _frame(
        OP_AI_COMPLETE,
        messages=[{"role": "user", "content": "Hi"}],
        org_id=str(org_id),
        input_files=[],
        execution_id=str(uuid4()),  # forged child claim: must be ignored
        actor_email="forged@example.com",
    )
    with patches[0], patches[1], patches[2]:
        resp = await _dispatch(db_session, principal, frame)
    assert resp["ok"] is True, resp
    assert record.await_count == 1
    kwargs = record.await_args.kwargs
    assert kwargs["user_id"] == SYSTEM_USER_UUID
    assert kwargs["organization_id"] == org_id
    assert str(kwargs["execution_id"]) == parent_exec


@pytest.mark.asyncio
async def test_complete_usage_commit_persists_row(db_session):
    """Usage is flush-only in the service: the dispatcher must commit."""
    from src.models.orm.ai_usage import AIUsage
    from src.models.orm.executions import Execution
    from src.models.orm.organizations import Organization

    org = Organization(
        id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="ai-local-test"
    )
    db_session.add(org)
    await db_session.flush()
    org_id = org.id
    execution_id = uuid4()
    db_session.add(
        Execution(
            id=execution_id,
            workflow_name="ai-local-test",
            executed_by_name="ai-local-test",
            organization_id=org_id,
        )
    )
    await db_session.flush()
    # Commit seeds: the shared service releases its connection before the
    # provider call, so uncommitted rows would be invisible to the usage
    # write on reacquire (the outer test transaction still rolls back).
    await db_session.commit()
    principal = _workflow_principal(
        organization={"id": str(org_id)}, execution_id=str(execution_id)
    )
    client = _fake_client()
    import src.services.llm as llm_pkg

    with (
        patch.object(
            llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)
        ),
        patch(
            "src.core.cache.get_shared_redis",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "src.services.model_registry.get_display_name",
            new=AsyncMock(return_value="gpt-4o"),
        ),
        patch(
            "src.services.ai_usage_service.get_cached_pricing",
            new=AsyncMock(return_value=(None, None, None, None)),
        ),
        patch(
            "src.services.ai_usage_service._notify_missing_pricing",
            new=AsyncMock(),
        ),
        patch(
            "src.services.ai_usage_service.invalidate_usage_cache",
            new=AsyncMock(),
        ),
        patch(
            "src.services.ai_usage_service._add_used_model",
            new=AsyncMock(),
        ),
    ):
        resp = await _dispatch(
            db_session,
            principal,
            _frame(
                OP_AI_COMPLETE,
                messages=[{"role": "user", "content": "Hi"}],
                org_id=str(org_id),
                input_files=[],
            ),
        )
    assert resp["ok"] is True, resp
    rows = (
        await db_session.execute(
            select(AIUsage).where(AIUsage.execution_id == execution_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].organization_id == org_id


# =============================================================================
# Provider failure mapping (status preserved, never generic 500)
# =============================================================================


@pytest.mark.asyncio
async def test_complete_provider_auth_maps_401(db_session):
    class AuthenticationError(Exception):
        pass

    AuthenticationError.__module__ = "anthropic"
    client = AsyncMock()
    client.provider_name = "anthropic"
    client.model_name = "claude-x"
    client.complete.side_effect = AuthenticationError("bad key")
    patches = _patch_provider(client)
    with patches[0], patches[1], patches[2]:
        resp = await _dispatch(
            db_session,
            _workflow_principal(execution_id=str(uuid4())),
            _frame(
                OP_AI_COMPLETE,
                messages=[{"role": "user", "content": "Hi"}],
                input_files=[],
            ),
        )
    assert resp["ok"] is False
    assert resp["status"] == 401
    assert "Anthropic" in resp["detail"]


@pytest.mark.asyncio
async def test_complete_value_error_maps_503(db_session):
    import src.services.llm as llm_pkg

    with patch.object(
        llm_pkg, "get_llm_client", new=AsyncMock(side_effect=ValueError("no profile"))
    ):
        resp = await _dispatch(
            db_session,
            _workflow_principal(execution_id=str(uuid4())),
            _frame(
                OP_AI_COMPLETE,
                messages=[{"role": "user", "content": "Hi"}],
                input_files=[],
            ),
        )
    assert resp["ok"] is False
    assert resp["status"] == 503
    assert resp["detail"] == "no profile"


@pytest.mark.asyncio
async def test_complete_generic_error_is_sanitized_500(db_session):
    client = AsyncMock()
    client.provider_name = "openai"
    client.model_name = "gpt-4o"
    client.complete.side_effect = RuntimeError("secret boom")
    patches = _patch_provider(client)
    with patches[0], patches[1], patches[2]:
        resp = await _dispatch(
            db_session,
            _workflow_principal(execution_id=str(uuid4())),
            _frame(
                OP_AI_COMPLETE,
                messages=[{"role": "user", "content": "Hi"}],
                input_files=[],
            ),
        )
    assert resp["ok"] is False
    assert resp["status"] == 500
    assert resp["detail"] == "AI completion failed. See server logs for details."
    assert "boom" not in resp["detail"]


# =============================================================================
# ai.model_info
# =============================================================================


@pytest.mark.asyncio
async def test_model_info_success_parity_with_http(db_session):
    from src.routers.cli import cli_ai_info

    principal = _workflow_principal(execution_id=str(uuid4()))
    config = SimpleNamespace(provider="openai", model="gpt-4o")
    with patch(
        "src.services.llm.factory.get_llm_config",
        new=AsyncMock(return_value=config),
    ):
        local = await _dispatch(
            db_session, principal, _frame(OP_AI_MODEL_INFO)
        )
        http_resp = await cli_ai_info(
            SimpleNamespace(user_id=uuid4()), AsyncMock()
        )
    assert local["ok"] is True, local
    assert local["result"] == {"provider": "openai", "model": "gpt-4o"}
    assert http_resp.provider == "openai"
    assert http_resp.model == "gpt-4o"


@pytest.mark.asyncio
async def test_model_info_missing_config_maps_404(db_session):
    with patch(
        "src.services.llm.factory.get_llm_config",
        new=AsyncMock(side_effect=ValueError("no AI config")),
    ):
        resp = await _dispatch(
            db_session,
            _workflow_principal(execution_id=str(uuid4())),
            _frame(OP_AI_MODEL_INFO),
        )
    assert resp["ok"] is False
    assert resp["status"] == 404
    assert resp["detail"] == "no AI config"


# =============================================================================
# SDK facade mapping (no DB)
# =============================================================================


def _local_error(status, detail="denied"):
    from bifrost.client import raise_for_status_with_detail

    request = httpx.Request("POST", "local://sdk/ai/complete")
    response = httpx.Response(status, json={"detail": detail}, request=request)
    try:
        raise_for_status_with_detail(response)
    except Exception as e:  # noqa: BLE001 - re-raised below by the fake
        return e
    raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_facade_complete_calls_local_without_http():
    from bifrost.ai import ai as ai_facade

    body = {
        "content": "Hello",
        "input_tokens": 3,
        "output_tokens": 5,
        "model": "gpt-4o",
    }
    transport = AsyncMock()
    transport.call_ai_complete.return_value = body
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        result = await ai_facade.complete("Hi")
    assert result.content == "Hello"
    assert result.model == "gpt-4o"
    assert transport.call_ai_complete.await_count == 1
    args, kwargs = transport.call_ai_complete.call_args
    assert args[0] == [{"role": "user", "content": "Hi"}]
    assert kwargs["timeout"] is None


@pytest.mark.asyncio
async def test_facade_complete_derives_no_extra_http_for_structured():
    from pydantic import BaseModel

    from bifrost.ai import ai as ai_facade

    class Answer(BaseModel):
        answer: str

    transport = AsyncMock()
    transport.call_ai_complete.return_value = {
        "content": '{"answer": "yes"}',
        "input_tokens": 1,
        "output_tokens": 1,
        "model": "gpt-4o",
    }
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        result = await ai_facade.complete("Q?", response_format=Answer)
    assert isinstance(result, Answer)
    assert result.answer == "yes"
    sent_messages = transport.call_ai_complete.call_args.args[0]
    assert "JSON" in sent_messages[0]["content"]


@pytest.mark.asyncio
async def test_facade_complete_knowledge_composition_stays_local():
    """knowledge= searches through the local knowledge facade first."""
    from bifrost import knowledge
    from bifrost.ai import ai as ai_facade
    from bifrost.models import KnowledgeDocument

    doc = KnowledgeDocument(
        id=str(uuid4()),
        namespace="policies",
        content="Refunds within 30 days.",
        metadata={},
        score=0.9,
        organization_id=None,
        key="refund",
        created_at=None,
    )
    transport = AsyncMock()
    transport.call_ai_complete.return_value = {
        "content": "ok",
        "input_tokens": 1,
        "output_tokens": 1,
        "model": "gpt-4o",
    }
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch.object(
            knowledge,
            "search",
            new=AsyncMock(return_value=[doc]),
        ) as search,
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        result = await ai_facade.complete("Refund policy?", knowledge=["policies"])
    assert result.content == "ok"
    search.assert_awaited_once()
    sent_messages = transport.call_ai_complete.call_args.args[0]
    assert any(
        "Refunds within 30 days" in m.get("content", "") for m in sent_messages
    )


@pytest.mark.asyncio
async def test_facade_complete_forwards_timeout_and_files():
    from bifrost.ai import ai as ai_facade
    from bifrost.models import AIInputFile

    transport = AsyncMock()
    transport.call_ai_complete.return_value = {
        "content": "ok",
        "input_tokens": 1,
        "output_tokens": 1,
        "model": "gpt-4o",
    }
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        await ai_facade.complete(
            "Summarize",
            profile="Reasoning",
            model="gpt-4o",
            max_tokens=11,
            timeout=60.0,
            files=[AIInputFile(filename="a.txt", content_type="text/plain", data=b"x")],
        )
    args, kwargs = transport.call_ai_complete.call_args
    assert args[1] == 11  # max_tokens
    assert args[3] == "Reasoning"  # profile
    assert args[4] == "gpt-4o"  # model
    assert kwargs["timeout"] == 60.0
    assert args[6][0]["filename"] == "a.txt"


@pytest.mark.asyncio
async def test_facade_complete_never_falls_back_to_http():
    from bifrost._local_transport import LocalTransportClosed
    from bifrost.ai import ai as ai_facade

    transport = AsyncMock()
    transport.call_ai_complete.side_effect = LocalTransportClosed("closed")
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        with pytest.raises(LocalTransportClosed):
            await ai_facade.complete("Hi")


@pytest.mark.asyncio
async def test_facade_complete_local_error_text_matches_http():
    from bifrost.ai import ai as ai_facade

    transport = AsyncMock()
    transport.call_ai_complete.side_effect = _local_error(503, "no profile")
    with patch("bifrost._local_transport.get", return_value=transport):
        with pytest.raises(RuntimeError, match="AI completion failed: no profile"):
            await ai_facade.complete("Hi")


@pytest.mark.asyncio
async def test_facade_model_info_calls_local_without_http():
    from bifrost.ai import ai as ai_facade

    transport = AsyncMock()
    transport.call_ai_model_info.return_value = {
        "provider": "openai",
        "model": "gpt-4o",
    }
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        info = await ai_facade.get_model_info()
    assert info == {"provider": "openai", "model": "gpt-4o"}
    transport.call_ai_model_info.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_facade_model_info_local_error_text_matches_http():
    from bifrost.ai import ai as ai_facade

    transport = AsyncMock()
    transport.call_ai_model_info.side_effect = _local_error(404, "no AI config")
    with patch("bifrost._local_transport.get", return_value=transport):
        with pytest.raises(
            RuntimeError, match="Failed to get AI model info: no AI config"
        ):
            await ai_facade.get_model_info()


@pytest.mark.asyncio
async def test_external_http_path_unchanged():
    from bifrost.ai import ai as ai_facade

    body = {
        "content": "Hello",
        "input_tokens": 2,
        "output_tokens": 2,
        "model": "gpt-4o",
    }

    def _response(status, payload):
        request = httpx.Request("POST", "http://test.local/api/sdk/ai/complete")
        return httpx.Response(status, json=payload, request=request)

    client = AsyncMock()
    client.post.return_value = _response(200, body)
    with (
        patch("bifrost._local_transport.get", return_value=None),
        patch("bifrost.ai.get_client", return_value=client),
    ):
        result = await ai_facade.complete("Hi")
    assert result.content == "Hello"
    sent = client.post.await_args.kwargs["json"]
    assert sent["messages"] == [{"role": "user", "content": "Hi"}]
    assert sent["input_files"] == []
