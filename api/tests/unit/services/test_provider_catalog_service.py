import httpx
import pytest

from src.services.provider_catalog_service import list_openrouter_models


@pytest.mark.asyncio
async def test_openrouter_catalog_merges_text_image_and_video_models():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "text-model",
                            "name": "Text Model",
                            "architecture": {"output_modalities": ["text"]},
                        },
                        {
                            "id": "shared-model",
                            "name": "Shared Model",
                            "architecture": {"output_modalities": ["text"]},
                        },
                    ]
                },
            )
        if request.url.path == "/api/v1/images/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "image-model", "name": "Image Model"}]},
            )
        if request.url.path == "/api/v1/videos/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "shared-model", "name": "Shared Model"}]},
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = await list_openrouter_models("sk-test", client)

    by_id = {model.id: model for model in models}
    assert by_id["text-model"].output_modalities == ["text"]
    assert by_id["image-model"].output_modalities == ["image"]
    assert by_id["shared-model"].output_modalities == ["text", "video"]
    assert [model.display_name for model in models] == [
        "Image Model",
        "Shared Model",
        "Text Model",
    ]
