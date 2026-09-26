"""Focused service tests for the SDK artifact generation extraction.

Covers the shared service (``shared.sdk_artifact_generation``) used by
the HTTP generation routes for ``artifacts.create_document``,
``artifacts.create_spreadsheet``, ``artifacts.create_text``, and
``artifacts.create_image``:

- actor ownership/org scope flow into storage unchanged
- document image resolution keeps caller scope; non-images are 422
- missing workspace images propagate as ValueError (global 422 shape)
- spreadsheet/text render in a worker thread then store
- image generation releases its config-read session before provider HTTP,
  then stores and records usage with execution/org/user scope
- the service takes an explicit trusted actor — never a Request or a
  child-provided actor claim
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.sdk_artifact_generation import (
    sdk_generate_image_artifact,
    sdk_render_document_artifact,
    sdk_render_spreadsheet_artifact,
    sdk_render_text_artifact,
)
from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError
from src.core.principal import UserPrincipal
from src.models.contracts.artifacts import (
    DocumentArtifactSpec,
    ImageArtifactSpec,
    SpreadsheetArtifactSpec,
    TextArtifactSpec,
)
from src.models.orm import Artifact
from src.services.artifacts import ArtifactAccessError
from sqlalchemy import text
from shared.sdk_artifact_generation import _isolated_session

ORG_ID = uuid4()


@pytest.mark.asyncio
async def test_isolated_session_uses_the_async_pool(db_session) -> None:
    async with _isolated_session(db_session) as read_db:
        assert read_db.bind is db_session.bind
        assert (await read_db.execute(text("SELECT 1"))).scalar_one() == 1


def _user(**kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk@example.com"),
        organization_id=kwargs.get("organization_id", ORG_ID),
        name="SDK",
        is_active=True,
        is_superuser=kwargs.get("is_superuser", False),
        is_verified=True,
    )


def _caller(**kwargs) -> ArtifactCaller:
    return ArtifactCaller(user=kwargs.get("user", _user()), db=MagicMock())


def _orm_artifact(**kwargs) -> Artifact:
    return Artifact(
        id=kwargs.get("id", uuid4()),
        organization_id=kwargs.get("organization_id", ORG_ID),
        created_by_user_id=kwargs.get("created_by_user_id", uuid4()),
        workspace_id=kwargs.get("workspace_id"),
        logical_path=kwargs.get("logical_path"),
        s3_key=kwargs.get("s3_key", "_artifacts/artifact-file"),
        filename=kwargs.get("filename", "Brief.pdf"),
        content_type=kwargs.get("content_type", "application/pdf"),
        size_bytes=kwargs.get("size_bytes", 8),
    )


def _document_request() -> DocumentArtifactSpec:
    return DocumentArtifactSpec(
        filename="brief",
        format="pdf",
        title="Brief",
        sections=[{"heading": "Summary", "paragraphs": ["Ready"]}],
    )


@pytest.mark.asyncio
async def test_document_stores_with_actor_scope() -> None:
    user = _user()
    caller = _caller(user=user)
    stored = _orm_artifact(filename="Brief.pdf")
    service = MagicMock()
    service.resolve_workspace_path = AsyncMock()
    service.read = AsyncMock()
    service.store = AsyncMock(return_value=stored)
    with patch(
        "src.services.artifacts.ArtifactService", return_value=service
    ) as cls:
        ref = await sdk_render_document_artifact(
            caller, spec=_document_request()
        )
    cls.assert_called_once_with(caller.db)
    service.resolve_workspace_path.assert_not_called()
    assert service.store.await_args.kwargs["created_by_user_id"] == user.user_id
    assert service.store.await_args.kwargs["organization_id"] == user.organization_id
    assert service.store.await_args.kwargs["workspace_id"] is None
    assert (
        service.store.await_args.kwargs["logical_path"]
        == service.store.await_args.kwargs["filename"]
    )
    assert ref.id == str(stored.id)


@pytest.mark.asyncio
async def test_document_resolves_images_with_caller_scope() -> None:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    user = _user()
    caller = _caller(user=user)
    workspace_id = uuid4()
    stored_image = _orm_artifact(
        filename="Portrait.png", content_type="image/png"
    )
    stored_doc = _orm_artifact(filename="Brief.pdf")
    service = MagicMock()
    service.resolve_workspace_path = AsyncMock(return_value=stored_image)
    service.read = AsyncMock(return_value=png_bytes)
    service.store = AsyncMock(return_value=stored_doc)
    request = DocumentArtifactSpec(
        filename="brief",
        format="pdf",
        title="Brief",
        sections=[
            {
                "heading": "Visual",
                "images": [{"path": "Portrait.png"}],
            }
        ],
    )
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        ref = await sdk_render_document_artifact(
            caller, spec=request, workspace_id=workspace_id
        )
    service.resolve_workspace_path.assert_awaited_once_with(
        workspace_id,
        "Portrait.png",
        user_id=user.user_id,
        organization_id=user.organization_id,
        is_platform_admin=False,
    )
    service.read.assert_awaited_once_with(stored_image)
    assert ref.id == str(stored_doc.id)


@pytest.mark.asyncio
async def test_document_non_image_is_422_without_storing() -> None:
    caller = _caller()
    stored_doc = _orm_artifact(filename="Notes.txt", content_type="text/plain")
    service = MagicMock()
    service.resolve_workspace_path = AsyncMock(return_value=stored_doc)
    service.read = AsyncMock()
    service.store = AsyncMock()
    request = DocumentArtifactSpec(
        filename="brief",
        format="pdf",
        title="Brief",
        sections=[{"heading": "Visual", "images": [{"path": "Notes.txt"}]}],
    )
    with (
        patch("src.services.artifacts.ArtifactService", return_value=service),
        pytest.raises(SdkArtifactError) as exc,
    ):
        await sdk_render_document_artifact(
            caller, spec=request, workspace_id=uuid4()
        )
    assert exc.value.status_code == 422
    assert "is not an image artifact" in str(exc.value.detail)
    service.store.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_missing_image_propagates_value_error() -> None:
    caller = _caller()
    service = MagicMock()
    service.resolve_workspace_path = AsyncMock(
        side_effect=ArtifactAccessError("Artifact workspace path x was not found.")
    )
    service.store = AsyncMock()
    request = DocumentArtifactSpec(
        filename="brief",
        format="pdf",
        title="Brief",
        sections=[{"heading": "Visual", "images": [{"path": "Missing.png"}]}],
    )
    with (
        patch("src.services.artifacts.ArtifactService", return_value=service),
        pytest.raises(ValueError, match="was not found"),
    ):
        await sdk_render_document_artifact(
            caller, spec=request, workspace_id=uuid4()
        )
    service.store.assert_not_awaited()


@pytest.mark.asyncio
async def test_spreadsheet_stores_with_actor_scope() -> None:
    user = _user()
    caller = _caller(user=user)
    stored = _orm_artifact(
        filename="Report.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    service = MagicMock()
    service.store = AsyncMock(return_value=stored)
    request = SpreadsheetArtifactSpec(
        filename="report",
        sheets=[{"name": "Data", "columns": ["A"], "rows": [["1"]]}],
    )
    with patch(
        "src.services.artifacts.ArtifactService", return_value=service
    ) as cls:
        ref = await sdk_render_spreadsheet_artifact(caller, spec=request)
    cls.assert_called_once_with(caller.db)
    assert service.store.await_args.kwargs["created_by_user_id"] == user.user_id
    assert service.store.await_args.kwargs["organization_id"] == user.organization_id
    assert ref.id == str(stored.id)


@pytest.mark.asyncio
async def test_text_stores_with_actor_scope() -> None:
    user = _user()
    caller = _caller(user=user)
    stored = _orm_artifact(filename="Notes.md", content_type="text/markdown")
    service = MagicMock()
    service.store = AsyncMock(return_value=stored)
    request = TextArtifactSpec(
        filename="notes", format="markdown", content="# Ready"
    )
    with patch(
        "src.services.artifacts.ArtifactService", return_value=service
    ) as cls:
        ref = await sdk_render_text_artifact(caller, spec=request)
    cls.assert_called_once_with(caller.db)
    assert service.store.await_args.kwargs["created_by_user_id"] == user.user_id
    assert service.store.await_args.kwargs["organization_id"] == user.organization_id
    assert ref.id == str(stored.id)


@pytest.mark.asyncio
async def test_image_generates_stores_and_records_usage() -> None:
    from contextlib import asynccontextmanager

    from shared.artifact_generation import GeneratedArtifact
    from src.services.media_generation import MediaProviderConfig

    user = _user()
    caller = _caller(user=user)
    workspace_id = uuid4()
    execution_id = uuid4()
    config = MediaProviderConfig(
        provider="openai",
        endpoint="https://api.openai.com/v1",
        api_key="secret",
        model="gpt-image-1",
        is_openrouter=False,
    )
    generated = GeneratedArtifact(
        filename="Launch Concept.png",
        content_type="image/png",
        content=b"png-data",
        provider="openai",
        model="gpt-image-1",
    )
    stored = _orm_artifact(
        filename="Launch Concept.png", content_type="image/png"
    )
    service = MagicMock()
    service.store = AsyncMock(return_value=stored)
    request = ImageArtifactSpec(
        filename="launch-concept", prompt="A launch concept"
    )
    config_db = MagicMock(name="config_db")

    @asynccontextmanager
    async def short_session(db):
        assert db is caller.db
        yield config_db

    with (
        patch(
            "shared.sdk_artifact_generation._isolated_session",
            side_effect=short_session,
        ) as isolated,
        patch(
            "src.services.media_generation.get_media_provider_config",
            AsyncMock(return_value=config),
        ) as resolve,
        patch(
            "src.services.media_generation.generate_image_with_config",
            AsyncMock(return_value=generated),
        ) as gen,
        patch(
            "src.services.media_generation.record_media_usage",
            AsyncMock(),
        ) as record,
        patch("src.services.artifacts.ArtifactService", return_value=service),
    ):
        ref = await sdk_generate_image_artifact(
            caller,
            spec=request,
            workspace_id=workspace_id,
            execution_id=execution_id,
        )
    # Config resolves on the short session, not the caller's session.
    assert isolated.call_count == 1
    resolve.assert_awaited_once_with(config_db, "image")
    gen.assert_awaited_once_with(
        config, filename="launch-concept", prompt="A launch concept"
    )
    service.store.assert_awaited_once_with(
        filename="Launch Concept.png",
        content_type="image/png",
        content=b"png-data",
        created_by_user_id=user.user_id,
        organization_id=user.organization_id,
        workspace_id=workspace_id,
        logical_path="Launch Concept.png",
    )
    record.assert_awaited_once_with(
        caller.db,
        generated,
        execution_id=execution_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
    )
    assert ref.id == str(stored.id)


@pytest.mark.asyncio
async def test_image_provider_starts_after_config_session_released() -> None:
    """Provider HTTP begins only after the config transaction released."""

    from contextlib import asynccontextmanager

    from shared.artifact_generation import GeneratedArtifact
    from src.services.media_generation import MediaProviderConfig

    caller = _caller()
    config = MediaProviderConfig(
        provider="openai",
        endpoint="https://api.openai.com/v1",
        api_key="secret",
        model="gpt-image-1",
        is_openrouter=False,
    )
    generated = GeneratedArtifact(
        filename="Launch Concept.png",
        content_type="image/png",
        content=b"png-data",
        provider="openai",
        model="gpt-image-1",
    )
    stored = _orm_artifact(
        filename="Launch Concept.png", content_type="image/png"
    )
    events: list[str] = []

    @asynccontextmanager
    async def short_session(_db):
        events.append("config-open")
        try:
            yield MagicMock(name="config_db")
        finally:
            events.append("config-released")

    async def fake_provider(cfg, *, filename, prompt):
        events.append("provider-start")
        assert "config-released" in events, (
            "provider HTTP must start after the config session released"
        )
        assert cfg is config
        return generated

    service = MagicMock()
    service.store = AsyncMock(return_value=stored)
    request = ImageArtifactSpec(
        filename="launch-concept", prompt="A launch concept"
    )
    with (
        patch(
            "shared.sdk_artifact_generation._isolated_session",
            side_effect=short_session,
        ),
        patch(
            "src.services.media_generation.get_media_provider_config",
            AsyncMock(return_value=config),
        ),
        patch(
            "src.services.media_generation.generate_image_with_config",
            side_effect=fake_provider,
        ),
        patch(
            "src.services.media_generation.record_media_usage",
            AsyncMock(),
        ),
        patch("src.services.artifacts.ArtifactService", return_value=service),
    ):
        await sdk_generate_image_artifact(caller, spec=request)
    assert events == ["config-open", "config-released", "provider-start"]


@pytest.mark.asyncio
async def test_document_render_starts_after_image_session_released() -> None:
    """CPU rendering begins only after the image-read session released."""

    import asyncio as std_asyncio
    from contextlib import asynccontextmanager

    caller = _caller()
    workspace_id = uuid4()
    stored_image = _orm_artifact(
        filename="Portrait.png", content_type="image/png"
    )
    stored_doc = _orm_artifact(filename="Brief.pdf")
    events: list[str] = []

    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(buffer, format="PNG")
    png_bytes = buffer.getvalue()

    @asynccontextmanager
    async def short_session(_db):
        events.append("images-open")
        try:
            yield MagicMock(name="read_db")
        finally:
            events.append("images-released")

    read_service = MagicMock()
    read_service.resolve_workspace_path = AsyncMock(return_value=stored_image)
    read_service.read = AsyncMock(return_value=png_bytes)
    store_service = MagicMock()
    store_service.store = AsyncMock(return_value=stored_doc)

    def service_factory(db):
        return read_service if db is not caller.db else store_service

    real_to_thread = std_asyncio.to_thread

    async def fake_to_thread(func, *args, **kwargs):
        events.append("render-start")
        assert "images-released" in events, (
            "rendering must start after the image session released"
        )
        return await real_to_thread(func, *args, **kwargs)

    request = DocumentArtifactSpec(
        filename="brief",
        format="pdf",
        title="Brief",
        sections=[
            {
                "heading": "Visual",
                "images": [{"path": "Portrait.png"}],
            }
        ],
    )
    with (
        patch(
            "shared.sdk_artifact_generation._isolated_session",
            side_effect=short_session,
        ),
        patch(
            "src.services.artifacts.ArtifactService", side_effect=service_factory
        ),
        patch(
            "shared.sdk_artifact_generation.asyncio.to_thread",
            side_effect=fake_to_thread,
        ),
    ):
        ref = await sdk_render_document_artifact(
            caller, spec=request, workspace_id=workspace_id
        )
    assert events == ["images-open", "images-released", "render-start"]
    read_service.resolve_workspace_path.assert_awaited_once()
    store_service.store.assert_awaited_once()
    assert ref.id == str(stored_doc.id)


@pytest.mark.asyncio
async def test_spreadsheet_does_not_touch_db_before_render() -> None:
    """Spreadsheet rendering checks out no DB connection early."""

    import asyncio as std_asyncio

    caller = _caller()
    stored = _orm_artifact(
        filename="Report.xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    service = MagicMock()
    service.store = AsyncMock(return_value=stored)
    request = SpreadsheetArtifactSpec(
        filename="report",
        sheets=[{"name": "Data", "columns": ["A"], "rows": [["1"]]}],
    )
    real_to_thread = std_asyncio.to_thread
    events: list[str] = []
    service_used_before_render = False

    def service_factory(db):
        nonlocal service_used_before_render
        if not events or events[-1] != "render-start":
            service_used_before_render = True
        assert db is caller.db
        return service

    async def fake_to_thread(func, *args, **kwargs):
        events.append("render-start")
        return await real_to_thread(func, *args, **kwargs)

    with (
        patch(
            "src.services.artifacts.ArtifactService", side_effect=service_factory
        ),
        patch(
            "shared.sdk_artifact_generation.asyncio.to_thread",
            side_effect=fake_to_thread,
        ),
    ):
        await sdk_render_spreadsheet_artifact(caller, spec=request)
    assert events == ["render-start"]
    assert not service_used_before_render
    service.store.assert_awaited_once()


def test_service_takes_explicit_actor_never_a_request() -> None:
    import shared.sdk_artifact_generation as service_module

    assert "fastapi" not in service_module.__name__
    for name in (
        "sdk_render_document_artifact",
        "sdk_render_spreadsheet_artifact",
        "sdk_render_text_artifact",
        "sdk_generate_image_artifact",
    ):
        params = set(inspect.signature(getattr(service_module, name)).parameters)
        assert "request" not in params, name
        assert "caller" in params, name
    source = inspect.getsource(service_module)
    assert "from fastapi" not in source
    assert "import fastapi" not in source
    assert "UploadFile" not in source
    assert "HTTPException(" not in source
