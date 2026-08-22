import httpx
import pytest
from types import SimpleNamespace

from src.models.contracts.artifacts import ModelCapabilities
from src.services.model_capabilities import (
    lookup_model_capabilities,
    model_fingerprint,
    normalize_capabilities,
    verify_model_capabilities,
)


@pytest.mark.asyncio
async def test_openrouter_catalog_maps_modalities_and_tools() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.url.params["output_modalities"] == "all"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "deepseek/deepseek-v4-pro",
                        "architecture": {
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                        },
                        "supported_parameters": ["tools", "tool_choice"],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        capabilities, message = await lookup_model_capabilities(
            provider="openai",
            model="deepseek/deepseek-v4-pro",
            endpoint="https://openrouter.ai/api/v1",
            client=client,
        )

    assert capabilities.source == "openrouter"
    assert capabilities.tool_calling is True
    assert capabilities.image_input is False
    assert capabilities.pdf_input is False
    assert "OpenRouter" in message


@pytest.mark.asyncio
async def test_openrouter_lookup_never_places_model_id_in_request_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "openrouter.ai"
        assert request.url.path == "/api/v1/models"
        assert "evil.test" not in str(request.url)
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        capabilities, _ = await lookup_model_capabilities(
            provider="openai",
            model="../../private?redirect=https://evil.test",
            endpoint="https://openrouter.ai/api/v1",
            client=client,
        )

    assert capabilities.source == "unknown"


@pytest.mark.asyncio
async def test_custom_endpoint_stays_unknown_without_authoritative_record() -> None:
    capabilities, message = await lookup_model_capabilities(
        provider="openai",
        model="private-model",
        endpoint="https://models.example.test/v1",
    )

    assert capabilities.source == "unknown"
    assert capabilities.tool_calling is False
    assert "manually" in message


def test_stale_capability_fingerprint_is_not_reused() -> None:
    stale = ModelCapabilities(
        tool_calling=True,
        source="manual",
        fingerprint=model_fingerprint(
            provider="openai", model="old-model", endpoint=None
        ),
    )

    normalized = normalize_capabilities(
        stale,
        provider="openai",
        model="new-model",
        endpoint=None,
    )

    assert normalized.source == "unknown"
    assert normalized.tool_calling is False
    assert normalized.fingerprint != stale.fingerprint


@pytest.mark.asyncio
async def test_provider_conformance_verifies_tool_image_and_pdf_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def complete(self, messages, tools=None, **kwargs):
            if kwargs.get("require_tool_call"):
                return SimpleNamespace(
                    tool_calls=[SimpleNamespace(name="capability_probe")]
                )
            input_files = messages[0].input_files
            if input_files:
                assert input_files[0].media_type in {"image/png", "application/pdf"}
            return SimpleNamespace(tool_calls=None)

    monkeypatch.setattr(
        "src.services.llm.factory.create_llm_client",
        lambda *args, **kwargs: FakeClient(),
    )

    capabilities, message = await verify_model_capabilities(
        provider="openai",
        model="private-model",
        endpoint="https://models.example.test/v1",
        api_key="test-key",
    )

    assert capabilities.source == "verified"
    assert capabilities.tool_calling is True
    assert capabilities.image_input is True
    assert capabilities.pdf_input is True
    assert capabilities.checked_at is not None
    assert "completed" in message
