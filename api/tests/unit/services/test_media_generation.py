import base64
from decimal import Decimal
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from shared.artifact_generation import GeneratedArtifact
from src.services.media_generation import (
    MediaGenerationError,
    MediaProviderConfig,
    generate_image,
    generate_video_with_config,
    record_media_usage,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


@pytest.mark.asyncio
async def test_generate_openrouter_image_returns_valid_artifact():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/images"
        assert request.headers["authorization"] == "Bearer secret"
        assert b'"model":"image/model"' in request.content
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": base64.b64encode(PNG_1X1).decode()}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 22, "cost": 0.04},
            },
        )

    config = MediaProviderConfig(
        provider="openai",
        endpoint="https://openrouter.ai/api/v1",
        api_key="secret",
        model="image/model",
        is_openrouter=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch(
            "src.services.media_generation.get_media_provider_config",
            AsyncMock(return_value=config),
        ):
            artifact = await generate_image(
                AsyncMock(),
                filename="launch_concept",
                prompt="A launch concept",
                client=client,
            )

    assert artifact.filename == "Launch Concept.png"
    assert artifact.content_type == "image/png"
    assert artifact.content == PNG_1X1
    assert artifact.provider == "openrouter"
    assert artifact.model == "image/model"
    assert artifact.input_tokens == 11
    assert artifact.output_tokens == 22
    assert artifact.provider_cost == Decimal("0.04")


@pytest.mark.asyncio
async def test_generate_openrouter_video_polls_and_downloads_content():
    requests: list[str] = []
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        requests.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "id": "video-job",
                    "polling_url": "/api/v1/videos/video-job",
                    "status": "pending",
                },
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(
                200,
                headers={"content-type": "video/mp4"},
                content=MP4_BYTES,
            )
        poll_count += 1
        if poll_count == 1:
            return httpx.Response(
                200,
                json={"id": "video-job", "status": "in_progress"},
            )
        return httpx.Response(
            200,
            json={
                "id": "video-job",
                "status": "completed",
                "usage": {"prompt_tokens": 7, "completion_tokens": 9, "cost": 0.12},
            },
        )

    reports: list[tuple[str, float | None]] = []

    async def report(phase: str, percent: float | None) -> None:
        reports.append((phase, percent))

    config = MediaProviderConfig(
        provider="openai",
        endpoint="https://openrouter.ai/api/v1",
        api_key="secret",
        model="video/model",
        is_openrouter=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch("src.services.media_generation.asyncio.sleep", AsyncMock()):
            artifact = await generate_video_with_config(
                config,
                filename="product_walkthrough",
                prompt="A product walkthrough",
                report=report,
                client=client,
            )

    assert requests == [
        "https://openrouter.ai/api/v1/videos",
        "https://openrouter.ai/api/v1/videos/video-job",
        "https://openrouter.ai/api/v1/videos/video-job",
        "https://openrouter.ai/api/v1/videos/video-job/content?index=0",
    ]
    assert artifact.filename == "Product Walkthrough.mp4"
    assert artifact.content == MP4_BYTES
    assert artifact.input_tokens == 7
    assert artifact.output_tokens == 9
    assert artifact.provider_cost == Decimal("0.12")
    assert reports[-1] == ("Video ready", 100)


@pytest.mark.asyncio
async def test_video_rejects_cross_host_polling_url():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            json={
                "id": "video-job",
                "polling_url": "https://example.com/steal-token",
            },
        )

    config = MediaProviderConfig(
        provider="openai",
        endpoint="https://openrouter.ai/api/v1",
        api_key="secret",
        model="video/model",
        is_openrouter=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MediaGenerationError, match="unsafe video status URL"):
            await generate_video_with_config(
                config,
                filename="video",
                prompt="prompt",
                report=AsyncMock(),
                client=client,
            )


@pytest.mark.asyncio
async def test_record_media_usage_uses_canonical_ai_ledger() -> None:
    conversation_id = uuid4()
    artifact = GeneratedArtifact(
        filename="Portrait.png",
        content_type="image/png",
        content=PNG_1X1,
        provider="openrouter",
        model="image/model",
        input_tokens=11,
        output_tokens=22,
        provider_cost=Decimal("0.04"),
    )
    record = AsyncMock()
    redis = AsyncMock()
    with (
        patch("src.core.cache.get_shared_redis", AsyncMock(return_value=redis)),
        patch("src.services.ai_usage_service.record_ai_usage", record),
    ):
        await record_media_usage(
            AsyncMock(),
            artifact,
            conversation_id=conversation_id,
        )

    record.assert_awaited_once_with(
        session=ANY,
        redis_client=redis,
        provider="openrouter",
        model="image/model",
        input_tokens=11,
        output_tokens=22,
        provider_cost=Decimal("0.04"),
        conversation_id=conversation_id,
        message_id=None,
        agent_run_id=None,
        execution_id=None,
        organization_id=None,
        user_id=None,
    )
