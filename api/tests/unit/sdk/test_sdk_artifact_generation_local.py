"""Engine-local transport for artifact generation SDK calls.

Covers the acceptance surface that does not need a forked child:

- HTTP/local parity for ``artifacts.create_document``,
  ``create_spreadsheet``, ``create_text``, and ``create_image`` through
  the shared service (``shared.sdk_artifact_generation``): input DTOs,
  ``ArtifactRef`` output, owner/workspace scope, renderer/provider
  errors, usage attribution, and content storage agree on both
  transports;
- the parent dispatcher derives actor, org, and execution identity
  from the parent-owned principal and never trusts child actor/org
  claims; the raw parent session commits on success and never on
  error;
- provider failure, scope misses, and malformed frames map to the
  same HTTP-style statuses as the HTTP path;
- timeout wiring: the image child deadline and parent dispatch bound
  clear the 180s provider HTTP window, render bounds cover CPU work,
  and cancellation propagates without committing — all exercised with
  events, never sleeps that mask a race;
- the migrated facade (Gate C4b) rides the shared
  ``BifrostClient.engine_request`` transport with the same request bodies
  and error mapping, and never touches the dedicated channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import multiprocessing
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from bifrost._local_transport import (
    ARTIFACT_IMAGE_LOCAL_TIMEOUT_SECONDS,
    ARTIFACT_RENDER_LOCAL_TIMEOUT_SECONDS,
    MAX_FRAME_BYTES,
    ChildLocalTransport,
)
from src.core.principal import UserPrincipal


class _MemoryStorage:
    """Deterministic in-memory stand-in for the file storage service."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def write_raw_to_s3(self, key: str, content: bytes) -> None:
        self.objects[key] = bytes(content)

    async def write_raw_chunks_to_s3(
        self, key: str, chunks, content_type: str = "application/octet-stream"
    ) -> None:
        buf = bytearray()
        async for chunk in chunks:
            buf.extend(chunk)
        self.objects[key] = bytes(buf)

    async def read_uploaded_file(self, key: str) -> bytes:
        return self.objects[key]

    async def generate_presigned_download_url(
        self,
        key: str,
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        return f"https://artifacts.local.test/{key}?sig=test"

    async def delete_raw_from_s3(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.fixture
def storage():
    store = _MemoryStorage()
    with patch(
        "src.services.artifacts.get_file_storage_service",
        return_value=store,
    ):
        yield store


async def _seed_org(db_session, *, is_provider: bool = False):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-gen-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-gen-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, org_id=None):
    from src.models.orm.users import User as UserModel

    row = UserModel(
        email=f"sdk-gen-{uuid4().hex[:8]}@test.local",
        name="SDK Gen Test",
        is_active=True,
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_system_user(db_session):
    from src.core.constants import SYSTEM_USER_UUID
    from src.models.orm.users import User as UserModel

    existing = await db_session.get(UserModel, SYSTEM_USER_UUID)
    if existing is not None:
        return existing
    row = UserModel(
        id=SYSTEM_USER_UUID,
        email="engine@bifrost.internal",
        name="Bifrost Engine",
        is_active=True,
        is_superuser=True,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    # Share the fixture transaction; keep the local commit from
    # persisting fixture users into later tests in this process.
    with patch.object(db_session, "commit", new_callable=AsyncMock):
        yield db_session


def _user(
    user_id: UUID,
    *,
    org_id: UUID | None = None,
    is_superuser: bool = False,
) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id,
        email="sdk-gen@test.local",
        organization_id=org_id,
        name="SDK Gen",
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
    )


def _principal(org_id: UUID | None = None, **kwargs):
    from src.services.execution.sdk_local_dispatch import LocalDispatchPrincipal

    return LocalDispatchPrincipal(
        caller_org_id=org_id,
        is_platform_admin=kwargs.get("is_platform_admin", False),
        is_provider_org=kwargs.get("is_provider_org", False),
        is_external=kwargs.get("is_external", False),
        actor_email=kwargs.get("actor_email", "sdk-gen@test.local"),
        is_service=kwargs.get("is_service", False),
        service_id=kwargs.get("service_id"),
        service_attempt_id=kwargs.get("service_attempt_id"),
        execution_id=kwargs.get("execution_id"),
    )


def _doc_body(**over):
    body = {
        "filename": "brief",
        "format": "pdf",
        "title": "Brief",
        "subtitle": None,
        "sections": [{"heading": "Summary", "paragraphs": ["Ready"]}],
        "page_size": "letter",
    }
    body.update(over)
    return body


def _sheet_body(**over):
    body = {
        "filename": "report",
        "sheets": [{"name": "Data", "columns": ["A"], "rows": [["1"]]}],
    }
    body.update(over)
    return body


def _text_body(**over):
    body = {"filename": "notes", "format": "markdown", "content": "# Ready"}
    body.update(over)
    return body


def _image_body(**over):
    body = {"filename": "launch-concept", "prompt": "A launch concept"}
    body.update(over)
    return body


async def _local_create(db_session, *, principal, op, body, workspace_id=None, execution_id=None):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame: dict = {
        "v": 1,
        "id": f"gen-{uuid4().hex[:8]}",
        "op": op,
        **body,
        "workspace_id": str(workspace_id) if workspace_id is not None else None,
    }
    if op == "artifacts.create_image":
        frame["execution_id"] = str(execution_id) if execution_id is not None else None
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


@pytest.mark.asyncio
class TestHttpLocalParity:
    async def test_document_parity(self, db_session, storage):
        from src.routers.cli import sdk_render_document_artifact
        from src.models.contracts.artifacts import DocumentArtifactSpec

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        principal = _principal(org.id)
        workspace_id = uuid4()

        http_ref = await sdk_render_document_artifact(
            DocumentArtifactSpec(**_doc_body()),
            user,
            workspace_id,
            db_session,
        )
        local = await _local_create(
            db_session, principal=principal, op="artifacts.create_document",
            body=_doc_body(), workspace_id=workspace_id,
        )
        assert local["ok"] is True, local
        assert local["result"]["filename"] == http_ref.filename
        assert local["result"]["content_type"] == http_ref.content_type
        assert local["result"]["size_bytes"] == http_ref.size_bytes
        assert local["result"]["type"] == "bifrost_artifact"

    async def test_spreadsheet_parity(self, db_session, storage):
        from src.routers.cli import sdk_render_spreadsheet_artifact
        from src.models.contracts.artifacts import SpreadsheetArtifactSpec

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        principal = _principal(org.id)
        workspace_id = uuid4()

        http_ref = await sdk_render_spreadsheet_artifact(
            SpreadsheetArtifactSpec(**_sheet_body()),
            user,
            workspace_id,
            db_session,
        )
        local = await _local_create(
            db_session, principal=principal, op="artifacts.create_spreadsheet",
            body=_sheet_body(), workspace_id=workspace_id,
        )
        assert local["ok"] is True, local
        assert local["result"]["filename"] == http_ref.filename
        assert local["result"]["content_type"] == http_ref.content_type
        assert local["result"]["size_bytes"] == http_ref.size_bytes

    async def test_text_parity(self, db_session, storage):
        from src.routers.cli import sdk_render_text_artifact
        from src.models.contracts.artifacts import TextArtifactSpec

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        principal = _principal(org.id)
        workspace_id = uuid4()

        http_ref = await sdk_render_text_artifact(
            TextArtifactSpec(**_text_body()),
            user,
            workspace_id,
            db_session,
        )
        local = await _local_create(
            db_session, principal=principal, op="artifacts.create_text",
            body=_text_body(), workspace_id=workspace_id,
        )
        assert local["ok"] is True, local
        assert local["result"]["filename"] == http_ref.filename
        assert local["result"]["content_type"] == http_ref.content_type
        assert local["result"]["size_bytes"] == http_ref.size_bytes

    async def test_image_parity_with_usage(self, db_session, storage):
        from src.routers.cli import sdk_generate_image_artifact
        from src.models.contracts.artifacts import ImageArtifactSpec
        from shared.artifact_generation import GeneratedArtifact
        from src.services.media_generation import MediaProviderConfig

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        workspace_id = uuid4()
        execution_id = uuid4()
        principal = _principal(org.id, execution_id=str(execution_id))
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

        with (
            patch(
                "src.services.media_generation.get_media_provider_config",
                AsyncMock(return_value=config),
            ),
            patch(
                "src.services.media_generation.generate_image_with_config",
                AsyncMock(return_value=generated),
            ),
            patch(
                "src.services.media_generation.record_media_usage",
                AsyncMock(),
            ) as record,
        ):
            http_ref = await sdk_generate_image_artifact(
                ImageArtifactSpec(**_image_body()),
                user,
                workspace_id,
                execution_id,
                db_session,
            )
            local = await _local_create(
                db_session, principal=principal, op="artifacts.create_image",
                body=_image_body(), workspace_id=workspace_id,
                execution_id=uuid4(),
            )
        assert local["ok"] is True, local
        assert local["result"]["filename"] == http_ref.filename
        assert local["result"]["content_type"] == http_ref.content_type
        # One usage record per transport. The local call uses the
        # parent-derived execution id even if the child supplies another.
        assert record.await_count == 2
        http_call = record.await_args_list[0]
        assert http_call.kwargs["organization_id"] == org.id
        assert http_call.kwargs["execution_id"] == execution_id
        local_call = record.await_args_list[1]
        assert local_call.kwargs["organization_id"] is None
        assert local_call.kwargs["execution_id"] == execution_id

    async def test_invalid_spec_is_422_both_paths(self, db_session, storage):
        import pydantic

        from src.models.contracts.artifacts import TextArtifactSpec

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)
        principal = _principal(org.id)

        # The HTTP path rejects an empty payload at request validation;
        # the local frame validates with the same DTO.
        with pytest.raises(pydantic.ValidationError):
            TextArtifactSpec(filename="notes", format="markdown", content="")

        local = await _local_create(
            db_session, principal=principal, op="artifacts.create_text",
            body=_text_body(content=""),
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_unknown_op_is_404(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        frame = {"v": 1, "id": "gen-unknown", "op": "artifacts.create_audio"}
        result = await dispatch_frame(
            lambda: _db_factory(db_session), _principal(), frame
        )
        assert result["ok"] is False
        assert result["status"] == 404

    async def test_malformed_workspace_is_422(self, db_session, storage):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _principal(is_platform_admin=True)
        bad_ws = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {"v": 1, "id": "bad-ws", "op": "artifacts.create_text",
             **_text_body(), "workspace_id": "nope"},
        )
        assert bad_ws["ok"] is False
        assert bad_ws["status"] == 422



@pytest.mark.asyncio
class TestRendererAndProviderErrors:
    async def test_document_non_image_is_422_and_does_not_commit(self, db_session, storage):
        from src.services.artifacts import ArtifactService

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)

        stored_doc = MagicMock()
        stored_doc.content_type = "text/plain"
        with patch.object(
            ArtifactService, "resolve_workspace_path", AsyncMock(return_value=stored_doc)
        ):
            local = await _local_create(
                db_session, principal=_principal(org.id),
                op="artifacts.create_document",
                body=_doc_body(sections=[
                    {"heading": "Visual", "images": [{"path": "Notes.txt"}]}
                ]),
                workspace_id=uuid4(),
            )
        assert local["ok"] is False
        assert local["status"] == 422
        assert "is not an image artifact" in local["detail"]

    async def test_document_missing_image_is_422(self, db_session, storage):
        from src.services.artifacts import ArtifactAccessError, ArtifactService

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)

        with patch.object(
            ArtifactService,
            "resolve_workspace_path",
            AsyncMock(side_effect=ArtifactAccessError("Artifact workspace path x was not found.")),
        ):
            local = await _local_create(
                db_session, principal=_principal(org.id),
                op="artifacts.create_document",
                body=_doc_body(sections=[
                    {"heading": "Visual", "images": [{"path": "Missing.png"}]}
                ]),
                workspace_id=uuid4(),
            )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_image_provider_failure_is_422(self, db_session, storage):
        from src.services.media_generation import (
            MediaGenerationError,
            MediaProviderConfig,
        )

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)
        config = MediaProviderConfig(
            provider="openai",
            endpoint="https://api.openai.com/v1",
            api_key="secret",
            model="gpt-image-1",
            is_openrouter=False,
        )
        with (
            patch(
                "src.services.media_generation.get_media_provider_config",
                AsyncMock(return_value=config),
            ),
            patch(
                "src.services.media_generation.generate_image_with_config",
                AsyncMock(side_effect=MediaGenerationError("provider blew up")),
            ),
        ):
            local = await _local_create(
                db_session, principal=_principal(org.id),
                op="artifacts.create_image", body=_image_body(),
                workspace_id=uuid4(), execution_id=uuid4(),
            )
        assert local["ok"] is False
        assert local["status"] == 422
        assert "provider blew up" in local["detail"]

    async def test_image_provider_not_configured_is_422(self, db_session, storage):
        from src.services.media_generation import MediaGenerationError

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)
        with patch(
            "src.services.media_generation.get_media_provider_config",
            AsyncMock(side_effect=MediaGenerationError(
                "Image generation is not configured in System Settings.")),
        ):
            local = await _local_create(
                db_session, principal=_principal(org.id),
                op="artifacts.create_image", body=_image_body(),
            )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_error_does_not_commit_success_does(self, db_session, storage):
        from src.services.media_generation import (
            MediaGenerationError,
            MediaProviderConfig,
        )

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)

        commits: list[str] = []

        @contextlib.asynccontextmanager
        async def factory():
            session = MagicMock()
            session.bind = db_session.bind
            session.commit = AsyncMock(side_effect=lambda: commits.append("commit"))
            yield session

        from src.services.execution.sdk_local_dispatch import dispatch_frame

        config = MediaProviderConfig(
            provider="openai", endpoint="https://api.openai.com/v1",
            api_key="secret", model="gpt-image-1", is_openrouter=False,
        )
        with (
            patch(
                "src.services.media_generation.get_media_provider_config",
                AsyncMock(return_value=config),
            ),
            patch(
                "src.services.media_generation.generate_image_with_config",
                AsyncMock(side_effect=MediaGenerationError("nope")),
            ),
        ):
            failed = await dispatch_frame(
                factory, _principal(org.id),
                {"v": 1, "id": "gen-nocommit", "op": "artifacts.create_image",
                 **_image_body(), "workspace_id": None, "execution_id": None},
            )
        assert failed["ok"] is False
        assert commits == []


@pytest.mark.asyncio
class TestScopeAndPrincipal:
    async def test_local_ignores_child_actor_and_org_claims(self, db_session, storage):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)
        workspace_id = uuid4()

        forged = await dispatch_frame(
            lambda: _db_factory(db_session),
            _principal(org.id),
            {
                "v": 1, "id": "forged-actor", "op": "artifacts.create_text",
                **_text_body(), "workspace_id": str(workspace_id),
                "actor_email": "attacker@test.local",
                "organization_id": str(uuid4()),
            },
        )
        assert forged["ok"] is True, forged

        from src.models.orm import Artifact as ArtifactModel
        from sqlalchemy import select

        rows = (await db_session.execute(
            select(ArtifactModel).where(ArtifactModel.workspace_id == workspace_id)
        )).scalars().all()
        assert len(rows) == 1
        from src.core.constants import SYSTEM_USER_UUID

        assert rows[0].created_by_user_id == SYSTEM_USER_UUID
        # The workflow engine principal is global (org None) — ownership
        # follows the parent-derived principal, never child claims.
        assert rows[0].organization_id is None

    async def test_service_principal_cannot_use_other_org_image(self, db_session, storage):
        """Image resolution keeps the parent-derived scope on both paths.

        The shared service opens its image-read session on a separate
        connection, which cannot see this test transaction's uncommitted
        rows — so scope is asserted at the service boundary instead:
        the service principal resolves with its own org and no admin
        bypass (an out-of-scope image 422s, like HTTP), while the
        workflow engine principal resolves with no org and the admin
        bypass.
        """
        import io

        from PIL import Image

        from src.services.artifacts import ArtifactAccessError, ArtifactService

        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), color="red").save(buffer, format="PNG")
        png_bytes = buffer.getvalue()

        org_a = await _seed_org(db_session)
        await _seed_org(db_session)
        await _seed_system_user(db_session)
        workspace_id = uuid4()
        stored_image = MagicMock()
        stored_image.content_type = "image/png"

        resolve_calls: list[dict] = []

        async def _resolve(workspace, path, *, user_id, organization_id, is_platform_admin):
            resolve_calls.append(
                {
                    "user_id": user_id,
                    "organization_id": organization_id,
                    "is_platform_admin": is_platform_admin,
                }
            )
            if is_platform_admin:
                return stored_image
            raise ArtifactAccessError(
                "Artifact workspace path Portrait.png was not found."
            )

        body = _doc_body(sections=[
            {"heading": "Visual", "images": [{"path": "Portrait.png"}]}
        ])
        with (
            patch.object(
                ArtifactService, "resolve_workspace_path", side_effect=_resolve
            ),
            patch.object(ArtifactService, "read", AsyncMock(return_value=png_bytes)),
        ):
            denied = await _local_create(
                db_session, principal=_principal(org_a.id, is_service=True),
                op="artifacts.create_document", body=body,
                workspace_id=workspace_id,
            )
            assert denied["ok"] is False
            assert denied["status"] == 422

            allowed = await _local_create(
                db_session, principal=_principal(org_a.id),
                op="artifacts.create_document", body=body,
                workspace_id=workspace_id,
            )
            assert allowed["ok"] is True, allowed

        from src.core.constants import SYSTEM_USER_UUID

        assert resolve_calls[0]["user_id"] == SYSTEM_USER_UUID
        assert resolve_calls[0]["organization_id"] == org_a.id
        assert resolve_calls[0]["is_platform_admin"] is False
        assert resolve_calls[1]["user_id"] == SYSTEM_USER_UUID
        assert resolve_calls[1]["organization_id"] is None
        assert resolve_calls[1]["is_platform_admin"] is True


class TestTimeoutsAndCancellation:
    def test_image_deadline_clears_provider_window(self):
        # Provider HTTP allows 180s; the parent bound and child
        # deadline clear it with room for store/commit + error frame.
        import src.services.execution.sdk_local_dispatch as dispatch

        assert dispatch.ARTIFACT_IMAGE_DISPATCH_TIMEOUT_SECONDS > 180.0
        assert (
            ARTIFACT_IMAGE_LOCAL_TIMEOUT_SECONDS
            > dispatch.ARTIFACT_IMAGE_DISPATCH_TIMEOUT_SECONDS
        )

    def test_render_bounds_are_bounded(self):
        import src.services.execution.sdk_local_dispatch as dispatch

        assert dispatch.ARTIFACT_RENDER_DISPATCH_TIMEOUT_SECONDS > 0
        assert (
            ARTIFACT_RENDER_LOCAL_TIMEOUT_SECONDS
            > dispatch.ARTIFACT_RENDER_DISPATCH_TIMEOUT_SECONDS
        )

    @pytest.mark.asyncio
    async def test_parent_image_timeout_returns_503(self, db_session, storage, monkeypatch):
        import src.services.execution.sdk_local_dispatch as dispatch
        from src.services.media_generation import MediaProviderConfig

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)
        # Shrink only the bound under test; the provider gate stays shut
        # so the timeout fires without a 180s sleep.
        monkeypatch.setattr(dispatch, "ARTIFACT_IMAGE_DISPATCH_TIMEOUT_SECONDS", 0.05)
        release = asyncio.Event()
        config = MediaProviderConfig(
            provider="openai", endpoint="https://api.openai.com/v1",
            api_key="secret", model="gpt-image-1", is_openrouter=False,
        )

        async def _hang(*args, **kwargs):
            await release.wait()

        with (
            patch(
                "src.services.media_generation.get_media_provider_config",
                AsyncMock(return_value=config),
            ),
            patch(
                "src.services.media_generation.generate_image_with_config",
                side_effect=_hang,
            ),
        ):
            try:
                local = await _local_create(
                    db_session, principal=_principal(org.id),
                    op="artifacts.create_image", body=_image_body(),
                )
            finally:
                release.set()
        assert local["ok"] is False
        assert local["status"] == 503

    @pytest.mark.asyncio
    async def test_parent_render_timeout_returns_503(self, db_session, storage, monkeypatch):
        import src.services.execution.sdk_local_dispatch as dispatch

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)
        monkeypatch.setattr(dispatch, "ARTIFACT_RENDER_DISPATCH_TIMEOUT_SECONDS", 0.05)
        release = asyncio.Event()

        async def _hang(*args, **kwargs):
            await release.wait()
            raise AssertionError("must not reach store after timeout")

        with patch(
            "shared.sdk_artifact_generation.sdk_render_text_artifact",
            side_effect=_hang,
        ):
            try:
                local = await _local_create(
                    db_session, principal=_principal(org.id),
                    op="artifacts.create_text", body=_text_body(),
                )
            finally:
                release.set()
        assert local["ok"] is False
        assert local["status"] == 503

    @pytest.mark.asyncio
    async def test_cancel_does_not_commit(self, db_session, storage):
        from src.services.execution.sdk_local_dispatch import dispatch_frame
        from src.services.media_generation import MediaProviderConfig

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        await _seed_user(db_session, org_id=org.id)
        entered = asyncio.Event()
        release = asyncio.Event()
        config = MediaProviderConfig(
            provider="openai", endpoint="https://api.openai.com/v1",
            api_key="secret", model="gpt-image-1", is_openrouter=False,
        )

        async def _hang(*args, **kwargs):
            entered.set()
            await release.wait()

        commits: list[str] = []

        @contextlib.asynccontextmanager
        async def factory():
            session = MagicMock()
            session.bind = db_session.bind
            session.commit = AsyncMock(side_effect=lambda: commits.append("commit"))
            yield session

        with (
            patch(
                "src.services.media_generation.get_media_provider_config",
                AsyncMock(return_value=config),
            ),
            patch(
                "src.services.media_generation.generate_image_with_config",
                side_effect=_hang,
            ),
        ):
            task = asyncio.create_task(
                dispatch_frame(
                    factory, _principal(org.id),
                    {"v": 1, "id": "gen-cancel", "op": "artifacts.create_image",
                     **_image_body(), "workspace_id": None, "execution_id": None},
                )
            )
            assert await asyncio.wait_for(entered.wait(), timeout=10.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
        assert commits == []


class TestChildTransport:
    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    async def _pump_ops(self, req_conn, resp_conn, handlers):
        for _ in range(len(handlers)):
            raw = await asyncio.to_thread(req_conn.recv_bytes, MAX_FRAME_BYTES + 1)
            frame = json.loads(raw.decode("utf-8"))
            response = handlers[frame["op"]](frame)
            await asyncio.to_thread(resp_conn.send_bytes, json.dumps(response).encode())

    @pytest.mark.asyncio
    async def test_all_four_ops_ride_engine_request_not_channel(self):
        import httpx

        from bifrost import _local_transport as lt
        from bifrost import artifacts as artifacts_mod

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        ref = {
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "Brief.pdf",
            "content_type": "application/pdf",
            "size_bytes": 8,
        }
        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=[httpx.Response(200, json=ref) for _ in range(4)]
        )
        try:
            from bifrost._context import set_execution_context
            from src.sdk.context import ExecutionContext

            workspace_id = uuid4()
            ctx = ExecutionContext(
                user_id="u1", email="e@e.com", name="T", scope="org-1",
                organization=None, is_platform_admin=False,
                is_function_key=False, execution_id="exec-1",
                artifact_workspace_id=str(workspace_id),
            )
            set_execution_context(ctx)
            try:
                with patch("bifrost.artifacts.get_client", return_value=client):
                    doc = await artifacts_mod.create_document(
                        "brief", format="pdf", title="Brief",
                        sections=[{"heading": "Summary", "paragraphs": ["Ready"]}],
                    )
                    assert doc.id == ref["id"]
                    sheet = await artifacts_mod.create_spreadsheet(
                        "report",
                        sheets=[{"name": "Data", "columns": ["A"], "rows": [["1"]]}],
                    )
                    assert sheet.id == ref["id"]
                    text = await artifacts_mod.create_text(
                        "notes", format="markdown", content="# Ready",
                    )
                    assert text.id == ref["id"]
                    image = await artifacts_mod.create_image(
                        "launch-concept", prompt="A launch concept",
                    )
                    assert image.id == ref["id"]
            finally:
                from bifrost._context import clear_execution_context

                clear_execution_context()
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

        assert [
            (call.args[0], call.args[1])
            for call in client.engine_request.await_args_list
        ] == [
            ("POST", "/api/sdk/artifacts/document"),
            ("POST", "/api/sdk/artifacts/spreadsheet"),
            ("POST", "/api/sdk/artifacts/text"),
            ("POST", "/api/sdk/artifacts/image"),
        ]

    @pytest.mark.asyncio
    async def test_error_surfaces_without_channel(self):
        import httpx

        from bifrost import _local_transport as lt
        from bifrost import artifacts as artifacts_mod
        from bifrost.client import BifrostAPIError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=[
                httpx.Response(
                    422,
                    json={"detail": "A document section must contain more."},
                    request=httpx.Request(
                        "POST", "http://api/api/sdk/artifacts/document"
                    ),
                ),
                httpx.Response(
                    422,
                    json={"detail": "A document section must contain more."},
                    request=httpx.Request(
                        "POST", "http://api/api/sdk/artifacts/text"
                    ),
                ),
            ]
        )
        try:
            with patch("bifrost.artifacts.get_client", return_value=client):
                with pytest.raises(BifrostAPIError):
                    await artifacts_mod.create_document(
                        "brief", format="pdf", title="Brief",
                        sections=[{"heading": "Summary", "paragraphs": ["Ready"]}],
                    )
                with pytest.raises(BifrostAPIError):
                    await artifacts_mod.create_text(
                        "notes", format="markdown", content="# Ready",
                    )
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        assert client.engine_request.await_count == 2

    @pytest.mark.asyncio
    async def test_image_timeout_breaks_channel_without_fallback(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(req_send, resp_recv)
        try:
            with pytest.raises(TimeoutError):
                await transport.call_artifacts_create_image(
                    "launch-concept", "A launch concept", timeout=0.05
                )
            with pytest.raises(Exception):
                await transport.call_artifacts_create_image(
                    "launch-concept", "A launch concept", timeout=0.05
                )
        finally:
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_large_text_round_trip_through_engine_request(self):
        """A >64KiB text body rides engine_request with no frame ceiling."""
        import httpx

        from bifrost import _local_transport as lt
        from bifrost import artifacts as artifacts_mod

        big_text = "# Big\n\n" + "lorem ipsum dolor sit amet\n" * 5000
        assert len(big_text) > 64 * 1024
        ref = {
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "Big.md",
            "content_type": "text/markdown",
            "size_bytes": len(big_text.encode()),
        }
        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        client = MagicMock()
        client.engine_request = AsyncMock(return_value=httpx.Response(200, json=ref))
        try:
            with patch("bifrost.artifacts.get_client", return_value=client):
                written = await artifacts_mod.create_text(
                    "Big.md", format="markdown", content=big_text,
                )
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        assert written.size_bytes > 64 * 1024
        assert (
            client.engine_request.await_args.kwargs["json"]["content"] == big_text
        )

    def test_child_module_imports_no_database(self):
        probe = textwrap.dedent(
            """
            import multiprocessing

            from bifrost import _local_transport as lt
            from bifrost import artifacts as _artifacts_mod

            a, b = multiprocessing.Pipe(duplex=False)
            c, d = multiprocessing.Pipe(duplex=False)
            lt.install(b, c)
            import sys

            forbidden = [
                m for m in sys.modules
                if m.startswith(("sqlalchemy", "asyncpg", "psycopg", "fastapi"))
                or m in ("src.models.orm", "src.core.database")
            ]
            assert not forbidden, forbidden
            assert hasattr(_artifacts_mod, "create_document")
            assert hasattr(_artifacts_mod, "create_spreadsheet")
            assert hasattr(_artifacts_mod, "create_text")
            assert hasattr(_artifacts_mod, "create_image")
            print("clean")
            """
        )
        api_root = Path(__file__).resolve().parents[3]
        env = {"PYTHONPATH": str(api_root), "PATH": "/usr/bin:/bin"}
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=api_root,
            env=env,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "clean" in result.stdout
