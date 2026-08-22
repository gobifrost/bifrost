import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


def _artifact_payload(
    artifact_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
) -> dict[str, object]:
    return {
        "type": "bifrost_artifact",
        "id": artifact_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
    }


@pytest.mark.asyncio
async def test_create_document_returns_server_artifact_reference(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")
    response = MagicMock()
    response.json.return_value = _artifact_payload(
        "artifact-1",
        "Brief.pdf",
        "application/pdf",
        8,
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    ref = await module.artifacts.create_document(
        "brief",
        format="pdf",
        title="Brief",
        sections=[{"heading": "Summary", "paragraphs": ["Ready"]}],
    )

    assert ref.type == "bifrost_artifact"
    assert ref.id == "artifact-1"
    assert ref.filename == "Brief.pdf"
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_stores_workflow_bytes_as_an_artifact(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")
    response = MagicMock()
    response.json.return_value = _artifact_payload(
        "artifact-written",
        "Processed Diagram.png",
        "image/png",
        8,
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    ref = await module.artifacts.write(
        "Processed Diagram.png",
        b"png-data",
        content_type="image/png",
    )

    assert ref.id == "artifact-written"
    client.post.assert_awaited_once_with(
        "/api/sdk/artifacts",
        files={"file": ("Processed Diagram.png", b"png-data", "image/png")},
        params={},
    )


@pytest.mark.asyncio
async def test_create_image_uses_media_endpoint(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")
    response = MagicMock()
    response.json.return_value = _artifact_payload(
        "artifact-image",
        "Launch Concept.png",
        "image/png",
        3,
    )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    ref = await module.artifacts.create_image(
        "launch-concept",
        prompt="A launch concept",
    )

    client.post.assert_awaited_once_with(
        "/api/sdk/artifacts/image",
        json={"filename": "launch-concept", "prompt": "A launch concept"},
        params={},
    )
    assert ref.id == "artifact-image"


@pytest.mark.asyncio
async def test_create_video_waits_for_durable_artifact_result(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")
    accepted = MagicMock()
    accepted.json.return_value = {"job_id": "job-1", "status": "queued"}
    completed = MagicMock()
    completed.json.return_value = {
        "id": "job-1",
        "status": "succeeded",
        "result": {
            "artifact": _artifact_payload(
                "artifact-video",
                "Launch Loop.mp4",
                "video/mp4",
                24,
            )
        },
    }
    client = MagicMock()
    client.post = AsyncMock(return_value=accepted)
    client.get = AsyncMock(return_value=completed)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    ref = await module.artifacts.create_video(
        "launch-loop",
        prompt="A launch loop",
        poll_interval_seconds=0.001,
    )

    client.post.assert_awaited_once_with(
        "/api/sdk/artifacts/video",
        json={"filename": "launch-loop", "prompt": "A launch loop"},
        params={},
    )
    client.get.assert_awaited_once_with("/api/platform-jobs/job-1")
    assert ref.id == "artifact-video"


@pytest.mark.asyncio
async def test_read_resolves_opaque_artifact_id(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")
    response = MagicMock(content=b"workbook")
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())

    data = await module.artifacts.read(
        _artifact_payload(
            "artifact-workbook",
            "Report.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            8,
        )
    )

    assert data == b"workbook"
    client.get.assert_awaited_once_with("/api/sdk/artifacts/artifact-workbook/content")


@pytest.mark.asyncio
async def test_list_uses_active_execution_workspace(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")
    response = MagicMock()
    response.json.return_value = [
        _artifact_payload("artifact-image", "Portrait.png", "image/png", 3)
    ]
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())
    context = MagicMock(artifact_workspace_id="workspace-1", execution_id="execution-1")
    token = module._execution_context.set(context)
    try:
        refs = await module.artifacts.list()
    finally:
        module._execution_context.reset(token)

    assert refs[0].filename == "Portrait.png"
    client.get.assert_awaited_once_with(
        "/api/sdk/artifacts",
        params={"workspace_id": "workspace-1", "execution_id": "execution-1"},
    )


@pytest.mark.asyncio
async def test_ai_media_helpers_return_the_same_artifact_contract(monkeypatch) -> None:
    ai_module = importlib.import_module("bifrost.ai")
    artifacts_module = importlib.import_module("bifrost.artifacts")
    expected = artifacts_module.ArtifactRef.model_validate(
        _artifact_payload("artifact-image", "Generated Image.png", "image/png", 3)
    )
    create_image = AsyncMock(return_value=expected)
    monkeypatch.setattr(artifacts_module.artifacts, "create_image", create_image)

    result = await ai_module.ai.create_image("A product diagram")

    assert result is expected
    create_image.assert_awaited_once_with(
        "A product diagram",
        prompt="A product diagram",
    )
