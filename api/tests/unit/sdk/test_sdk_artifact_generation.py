"""Focused service tests for the SDK artifact generation extraction.

Covers the shared service (``shared.sdk_artifact_generation``) used by
the HTTP generation routes for ``artifacts.create_document``,
``artifacts.create_spreadsheet``, ``artifacts.create_text``, and
``artifacts.create_image``:

- actor ownership/org scope flow into storage unchanged
- document image resolution keeps caller scope; non-images are 422
- missing workspace images propagate as ValueError (global 422 shape)
- spreadsheet/text render in a worker thread then store
- image generation calls the provider on the caller session, stores,
  then records usage with execution/org/user scope
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

ORG_ID = uuid4()


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
    from shared.artifact_generation import GeneratedArtifact

    user = _user()
    caller = _caller(user=user)
    workspace_id = uuid4()
    execution_id = uuid4()
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
    with (
        patch(
            "src.services.media_generation.generate_image",
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
    gen.assert_awaited_once_with(
        caller.db, filename="launch-concept", prompt="A launch concept"
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
