"""Migrated ``bifrost.ai`` facade tests over ``engine_request``.

Each method sends the exact HTTP method/path/body the external path used;
statuses map to the same public exceptions with no silent fallback. The
completion's timeout is forwarded verbatim, so omitting it means no
SDK-imposed deadline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest


def _ai_response(status, payload):
    request = httpx.Request("POST", "http://engine.local/api/sdk/ai/complete")
    return httpx.Response(status, json=payload, request=request)


class TestEngineRequestAIFacade:
    """Gate C5f: the migrated AI facade rides ``engine_request``.

    Each method sends the exact HTTP method/path/body the external path
    used, so the socket-served and network calls stay identical; statuses
    map to the same public exceptions with no silent fallback. The
    completion's timeout is forwarded verbatim, so omitting it means no
    SDK-imposed deadline and never forces a caller to pass one.
    """

    def _client(self, *responses):
        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_complete_uses_exact_http_call(self):
        from bifrost.ai import ai as ai_facade
        from bifrost.models import AIInputFile

        body = {
            "content": "Hello",
            "input_tokens": 3,
            "output_tokens": 5,
            "model": "gpt-4o",
        }
        client = self._client(_ai_response(200, body))
        with patch("bifrost.ai.get_client", return_value=client):
            result = await ai_facade.complete(
                "Hi",
                max_tokens=11,
                profile="Reasoning",
                model="gpt-4o",
                org_id=None,
                timeout=60.0,
                files=[
                    AIInputFile(
                        filename="a.txt", content_type="text/plain", data=b"x"
                    )
                ],
            )
        assert result.content == "Hello"
        assert result.model == "gpt-4o"
        assert result.input_tokens == 3
        assert result.output_tokens == 5
        args, kwargs = client.engine_request.await_args
        assert args == ("POST", "/api/sdk/ai/complete")
        assert kwargs["timeout"] == 60.0
        payload = kwargs["json"]
        assert payload["messages"] == [{"role": "user", "content": "Hi"}]
        assert payload["max_tokens"] == 11
        assert payload["profile"] == "Reasoning"
        assert payload["model"] == "gpt-4o"
        assert payload["input_files"][0]["filename"] == "a.txt"

    @pytest.mark.asyncio
    async def test_complete_omitted_timeout_forwards_none(self):
        """No mandatory developer timeout and no SDK-imposed 30s deadline."""
        from bifrost.ai import ai as ai_facade

        client = self._client(
            _ai_response(
                200,
                {
                    "content": "Hello",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "model": "gpt-4o",
                },
            )
        )
        with patch("bifrost.ai.get_client", return_value=client):
            await ai_facade.complete("Hi")
        _, kwargs = client.engine_request.await_args
        assert kwargs["timeout"] is None

    @pytest.mark.asyncio
    async def test_complete_structured_output_stays_on_facade(self):
        from pydantic import BaseModel

        from bifrost.ai import ai as ai_facade

        class Answer(BaseModel):
            answer: str

        client = self._client(
            _ai_response(
                200,
                {
                    "content": '{"answer": "yes"}',
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "model": "gpt-4o",
                },
            )
        )
        with patch("bifrost.ai.get_client", return_value=client):
            result = await ai_facade.complete("Q?", response_format=Answer)
        assert isinstance(result, Answer)
        assert result.answer == "yes"
        sent_messages = client.engine_request.await_args.kwargs["json"]["messages"]
        assert "JSON" in sent_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_complete_knowledge_composition_stays_on_facade(self):
        """knowledge= searches through the knowledge facade before the call."""
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
        client = self._client(
            _ai_response(
                200,
                {
                    "content": "ok",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "model": "gpt-4o",
                },
            )
        )
        with (
            patch("bifrost.ai.get_client", return_value=client),
            patch.object(
                knowledge, "search", new=AsyncMock(return_value=[doc])
            ) as search,
        ):
            result = await ai_facade.complete(
                "Refund policy?", knowledge=["policies"]
            )
        assert result.content == "ok"
        search.assert_awaited_once()
        sent_messages = client.engine_request.await_args.kwargs["json"]["messages"]
        assert any(
            "Refunds within 30 days" in m.get("content", "")
            for m in sent_messages
        )

    @pytest.mark.asyncio
    async def test_complete_error_text_matches_http(self):
        from bifrost.ai import ai as ai_facade

        client = self._client(_ai_response(503, {"detail": "no profile"}))
        with patch("bifrost.ai.get_client", return_value=client):
            with pytest.raises(RuntimeError, match="AI completion failed: no profile"):
                await ai_facade.complete("Hi")

    @pytest.mark.asyncio
    async def test_complete_cancellation_propagates(self):
        """Cancelling the awaiting task cancels the in-flight local request."""
        import asyncio

        from bifrost.ai import ai as ai_facade

        started = asyncio.Event()

        async def _hang(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=_hang)
        with patch("bifrost.ai.get_client", return_value=client):
            task = asyncio.create_task(ai_facade.complete("Hi"))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert client.engine_request.await_count == 1

    @pytest.mark.asyncio
    async def test_model_info_uses_exact_http_call(self):
        from bifrost.ai import ai as ai_facade

        client = self._client(
            _ai_response(200, {"provider": "openai", "model": "gpt-4o"})
        )
        with patch("bifrost.ai.get_client", return_value=client):
            info = await ai_facade.get_model_info()
        assert info == {"provider": "openai", "model": "gpt-4o"}
        args, kwargs = client.engine_request.await_args
        assert args == ("GET", "/api/sdk/ai/info")
        assert kwargs == {}

    @pytest.mark.asyncio
    async def test_model_info_error_text_matches_http(self):
        from bifrost.ai import ai as ai_facade

        client = self._client(_ai_response(404, {"detail": "no AI config"}))
        with patch("bifrost.ai.get_client", return_value=client):
            with pytest.raises(
                RuntimeError, match="Failed to get AI model info: no AI config"
            ):
                await ai_facade.get_model_info()
