"""Focused service tests for the SDK artifact core extraction.

Covers the shared service (``shared.sdk_artifacts``) used by the HTTP
artifact routes for ``artifacts.write``, ``artifacts.list``,
``artifacts.read``, and ``artifacts.get_download_url``:

- content validation runs before persistence (actor ownership/org scope)
- auth/scope misses surface as transport-neutral 404 (admin bypass kept)
- read returns exact bytes with inert attachment disposition
- list maps the latest workspace versions through unchanged
- download delegates to the signed-URL path (inert headers owned there)
- Office preview returns locked-down HTML; non-Office stays raw
- the service takes an explicit trusted actor — never a Request or a
  child-provided actor claim
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from shared.sdk_artifacts import (
    ArtifactCaller,
    SdkArtifactError,
    sdk_artifact_download_url,
    sdk_list_artifacts,
    sdk_read_artifact,
    sdk_store_artifact,
)
from src.core.principal import UserPrincipal
from src.models.orm import Artifact
from src.services.artifacts import ArtifactAccessError

ORG_ID = UUID("22222222-2222-2222-2222-222222222222")


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
    db = MagicMock()
    return ArtifactCaller(user=kwargs.get("user", _user()), db=db)


def _orm_artifact(**kwargs) -> Artifact:
    return Artifact(
        id=kwargs.get("id", uuid4()),
        organization_id=kwargs.get("organization_id", ORG_ID),
        created_by_user_id=kwargs.get("created_by_user_id", uuid4()),
        workspace_id=kwargs.get("workspace_id"),
        logical_path=kwargs.get("logical_path"),
        s3_key=kwargs.get("s3_key", "_artifacts/artifact-file"),
        filename=kwargs.get("filename", "Workflow Notes.md"),
        content_type=kwargs.get("content_type", "text/markdown"),
        size_bytes=kwargs.get("size_bytes", 7),
    )


@pytest.mark.asyncio
async def test_store_rejects_unsupported_content_before_persisting() -> None:
    caller = _caller()
    service = MagicMock()
    service.store = AsyncMock()
    with (
        patch("src.services.artifacts.ArtifactService", return_value=service),
        pytest.raises(ValueError, match="unsupported artifact type"),
    ):
        await sdk_store_artifact(
            caller,
            filename="payload.bin",
            content_type="application/octet-stream",
            content=b"data",
        )
    service.store.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_uses_actor_ownership_and_org_scope() -> None:
    user = _user()
    caller = _caller(user=user)
    workspace_id = uuid4()
    stored = _orm_artifact(
        filename="Workflow Notes.md",
        content_type="text/markdown",
        size_bytes=7,
        workspace_id=workspace_id,
        logical_path="Workflow Notes.md",
    )
    service = MagicMock()
    service.store = AsyncMock(return_value=stored)
    with patch("src.services.artifacts.ArtifactService", return_value=service) as cls:
        ref = await sdk_store_artifact(
            caller,
            filename="Workflow Notes.md",
            content_type="text/markdown",
            content=b"# Ready",
            workspace_id=workspace_id,
        )
    cls.assert_called_once_with(caller.db)
    service.store.assert_awaited_once_with(
        filename="Workflow Notes.md",
        content_type="text/markdown",
        content=b"# Ready",
        created_by_user_id=user.user_id,
        organization_id=user.organization_id,
        workspace_id=workspace_id,
        logical_path="Workflow Notes.md",
    )
    assert ref.id == str(stored.id)
    assert ref.filename == "Workflow Notes.md"
    assert ref.size_bytes == 7


@pytest.mark.asyncio
async def test_store_engine_superuser_carries_no_org() -> None:
    """The HTTP engine token is a global superuser; org stays None."""
    user = _user(is_superuser=True, organization_id=None)
    caller = _caller(user=user)
    stored = _orm_artifact(organization_id=None)
    service = MagicMock()
    service.store = AsyncMock(return_value=stored)
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        await sdk_store_artifact(
            caller,
            filename="Workflow Notes.md",
            content_type="text/markdown",
            content=b"# Ready",
        )
    assert service.store.await_args.kwargs["organization_id"] is None
    assert service.store.await_args.kwargs["created_by_user_id"] == user.user_id


@pytest.mark.asyncio
async def test_list_maps_latest_versions_with_caller_scope() -> None:
    user = _user()
    caller = _caller(user=user)
    workspace_id = uuid4()
    first = _orm_artifact(filename="Notes.md", content_type="text/markdown")
    second = _orm_artifact(filename="Portrait.png", content_type="image/png")
    service = MagicMock()
    service.list_workspace = AsyncMock(return_value=[first, second])
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        refs = await sdk_list_artifacts(caller, workspace_id=workspace_id)
    service.list_workspace.assert_awaited_once_with(
        workspace_id,
        user_id=user.user_id,
        organization_id=user.organization_id,
        is_platform_admin=False,
    )
    assert [ref.filename for ref in refs] == ["Notes.md", "Portrait.png"]
    assert all(ref.type == "bifrost_artifact" for ref in refs)


@pytest.mark.asyncio
async def test_read_returns_bytes_with_attachment_for_browser_active() -> None:
    caller = _caller()
    artifact = _orm_artifact(
        filename="Unsafe.html",
        content_type="text/html; charset=utf-8",
    )
    service = MagicMock()
    service.get_authorized = AsyncMock(return_value=artifact)
    service.read = AsyncMock(return_value=b"<h1>hi</h1>")
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        result = await sdk_read_artifact(caller, artifact_id=artifact.id)
    assert result.content == b"<h1>hi</h1>"
    assert result.content_type == "text/html; charset=utf-8"
    assert result.content_disposition == "attachment"
    assert result.preview_html is None


@pytest.mark.asyncio
async def test_read_inert_bytes_have_no_disposition() -> None:
    caller = _caller()
    artifact = _orm_artifact(filename="Portrait.png", content_type="image/png")
    service = MagicMock()
    service.get_authorized = AsyncMock(return_value=artifact)
    service.read = AsyncMock(return_value=b"png-data")
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        result = await sdk_read_artifact(caller, artifact_id=artifact.id)
    assert result.content == b"png-data"
    assert result.content_disposition is None


@pytest.mark.asyncio
async def test_read_out_of_scope_is_404() -> None:
    caller = _caller()
    service = MagicMock()
    service.get_authorized = AsyncMock(side_effect=ArtifactAccessError("Artifact not found."))
    with (
        patch("src.services.artifacts.ArtifactService", return_value=service),
        pytest.raises(SdkArtifactError) as exc,
    ):
        await sdk_read_artifact(caller, artifact_id=uuid4())
    assert exc.value.status_code == 404
    assert "not found" in str(exc.value.detail).lower()
    service.read.assert_not_called()


@pytest.mark.asyncio
async def test_read_preview_office_returns_html_not_bytes() -> None:
    from docx import Document

    import io

    buffer = io.BytesIO()
    document = Document()
    document.add_paragraph("Preview me")
    document.save(buffer)
    docx_bytes = buffer.getvalue()

    caller = _caller()
    artifact = _orm_artifact(
        filename="Brief.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    service = MagicMock()
    service.get_authorized = AsyncMock(return_value=artifact)
    service.read = AsyncMock(return_value=docx_bytes)
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        result = await sdk_read_artifact(
            caller, artifact_id=artifact.id, preview=True
        )
    assert result.preview_html is not None
    assert "Preview me" in result.preview_html
    assert result.content == docx_bytes


@pytest.mark.asyncio
async def test_read_preview_non_office_returns_raw_bytes() -> None:
    caller = _caller()
    artifact = _orm_artifact(filename="Portrait.png", content_type="image/png")
    service = MagicMock()
    service.get_authorized = AsyncMock(return_value=artifact)
    service.read = AsyncMock(return_value=b"png-data")
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        result = await sdk_read_artifact(
            caller, artifact_id=artifact.id, preview=True
        )
    assert result.preview_html is None
    assert result.content == b"png-data"


@pytest.mark.asyncio
async def test_download_url_delegates_to_signed_url_path() -> None:
    user = _user()
    caller = _caller(user=user)
    artifact = _orm_artifact(filename="Unsafe.html", content_type="text/html")
    service = MagicMock()
    service.get_authorized = AsyncMock(return_value=artifact)
    service.generate_download_url = AsyncMock(
        return_value="https://cdn.example/artifact?sig=1"
    )
    with patch("src.services.artifacts.ArtifactService", return_value=service):
        response = await sdk_artifact_download_url(caller, artifact_id=artifact.id)
    service.get_authorized.assert_awaited_once_with(
        artifact.id,
        user_id=user.user_id,
        organization_id=user.organization_id,
        is_platform_admin=False,
    )
    service.generate_download_url.assert_awaited_once_with(artifact)
    assert response.url == "https://cdn.example/artifact?sig=1"


@pytest.mark.asyncio
async def test_download_url_out_of_scope_is_404() -> None:
    caller = _caller()
    service = MagicMock()
    service.get_authorized = AsyncMock(side_effect=ArtifactAccessError("Artifact not found."))
    with (
        patch("src.services.artifacts.ArtifactService", return_value=service),
        pytest.raises(SdkArtifactError) as exc,
    ):
        await sdk_artifact_download_url(caller, artifact_id=uuid4())
    assert exc.value.status_code == 404
    service.generate_download_url.assert_not_called()


@pytest.mark.asyncio
async def test_from_context_carries_trusted_principal() -> None:
    ctx = MagicMock()
    ctx.user = _user()
    ctx.db = MagicMock()
    caller = ArtifactCaller.from_context(ctx)
    assert caller.user is ctx.user
    assert caller.db is ctx.db


def test_service_takes_explicit_actor_never_a_request() -> None:
    import shared.sdk_artifacts as service_module

    assert "fastapi" not in service_module.__name__
    for name in (
        "sdk_store_artifact",
        "sdk_list_artifacts",
        "sdk_read_artifact",
        "sdk_artifact_download_url",
    ):
        params = set(inspect.signature(getattr(service_module, name)).parameters)
        assert "request" not in params, name
        assert "caller" in params, name
    source = inspect.getsource(service_module)
    assert "from fastapi" not in source
    assert "import fastapi" not in source
    assert "UploadFile" not in source
    assert "HTTPException(" not in source
