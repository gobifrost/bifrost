"""Engine-local transport for the artifact core SDK calls.

Covers the acceptance surface that does not need a forked child:

- HTTP/local parity for ``artifacts.write``/``read``/``list``/
  ``get_download_url`` through the shared service
  (``shared.sdk_artifacts``): exact bytes, versioned refs, workspace
  scope, signed-URL semantics, and error/status behavior
  (404 scope misses, 422 validation) agree on both transports;
- the parent dispatcher derives user, organization, and capability from
  the parent-owned principal (workflow engine superuser vs. supervised
  service org confinement) and never trusts child actor/org claims;
- the service-token org boundary: a service principal cannot read
  another org's artifacts while the engine principal bypasses;
- the child transport performs real round trips over a dedicated channel
  with zero HTTP requests, safe concurrent calls, timeout/EOF handling,
  bounded frames, chunked large payloads, and no silent HTTP fallback.
"""

import asyncio
import base64
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
    MAX_FRAME_BYTES,
    ChildLocalTransport,
    decode_frame,
    encode_frame,
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
        url = f"https://artifacts.local.test/{key}?sig=test"
        if response_content_type is not None:
            url += f"&ct={response_content_type}"
        if response_content_disposition is not None:
            url += f"&cd={response_content_disposition}"
        return url

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
        name=f"sdk-artifact-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-artifact-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, org_id=None, email: str | None = None):
    from src.models.orm.users import User as UserModel

    row = UserModel(
        email=email or f"sdk-artifact-{uuid4().hex[:8]}@test.local",
        name="SDK Artifact Test",
        is_active=True,
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_system_user(db_session):
    """Seed the engine sentinel user row local writes attribute to.

    The parent dispatcher builds a token-equivalent principal with
    ``SYSTEM_USER_UUID`` (like the minted engine/service tokens), so
    local ``artifacts.write`` inserts carry that id in
    ``created_by_user_id`` (FK → ``users.id``). Production databases
    carry the sentinel row; tests seed it explicitly.
    """
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
    # Unit cases share the fixture transaction so they can compare HTTP and
    # local calls against the same rows. Keep the local write's commit from
    # persisting fixture users into later E2E tests in this pytest process;
    # the live-worker test verifies the real commit boundary.
    with patch.object(db_session, "commit", new_callable=AsyncMock):
        yield db_session


def _user(
    user_id: UUID,
    *,
    org_id: UUID | None = None,
    is_superuser: bool = False,
    email: str = "sdk-artifact@test.local",
) -> UserPrincipal:
    return UserPrincipal(
        user_id=user_id,
        email=email,
        organization_id=org_id,
        name="SDK Artifact",
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
        actor_email=kwargs.get("actor_email", "sdk-artifact@test.local"),
        is_service=kwargs.get("is_service", False),
        service_id=kwargs.get("service_id"),
        service_attempt_id=kwargs.get("service_attempt_id"),
    )


def _upload(filename: str, content: bytes, content_type: str):
    upload = MagicMock()
    upload.filename = filename
    upload.content_type = content_type
    upload.read = AsyncMock(return_value=content)
    return upload


async def _http_write(db_session, *, user, filename, content, content_type, workspace_id=None):
    from src.routers.cli import sdk_store_artifact

    return await sdk_store_artifact(
        user,
        _upload(filename, content, content_type),
        workspace_id,
        db_session,
    )


async def _http_list(db_session, *, user, workspace_id):
    from src.routers.cli import sdk_list_artifacts

    return await sdk_list_artifacts(user, workspace_id, db_session)


async def _http_read(db_session, *, user, artifact_id):
    from src.routers.cli import sdk_read_artifact

    response = await sdk_read_artifact(artifact_id, user, db_session)
    return response.body


async def _http_download_url(db_session, *, user, artifact_id):
    from src.routers.cli import sdk_artifact_download_url

    return await sdk_artifact_download_url(artifact_id, user, db_session)


async def _local_write(
    db_session, *, principal, filename, content, content_type, workspace_id=None
):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame: dict = {
        "v": 1,
        "id": f"art-write-{uuid4().hex[:8]}",
        "op": "artifacts.write",
        "filename": filename,
        "content_type": content_type,
        "content": base64.b64encode(content).decode("ascii"),
        "workspace_id": str(workspace_id) if workspace_id is not None else None,
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _local_read(db_session, *, principal, artifact_id):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame = {
        "v": 1,
        "id": f"art-read-{uuid4().hex[:8]}",
        "op": "artifacts.read",
        "artifact_id": str(artifact_id),
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _local_list(db_session, *, principal, workspace_id):
    from src.services.execution.sdk_local_dispatch import dispatch_frames

    frame = {
        "v": 1,
        "id": f"art-list-{uuid4().hex[:8]}",
        "op": "artifacts.list",
        "workspace_id": str(workspace_id),
    }
    frames = await dispatch_frames(lambda: _db_factory(db_session), principal, frame)
    collected = list(frames)
    first = collected[0]
    if not first.get("chunked"):
        return first
    assert len(collected) == first["parts"] + 1
    raw = b"".join(base64.b64decode(part["data"]) for part in collected[1:])
    assert len(raw) == first["total"]
    return {"ok": True, "result": json.loads(raw)}


async def _local_download_url(db_session, *, principal, artifact_id):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame = {
        "v": 1,
        "id": f"art-url-{uuid4().hex[:8]}",
        "op": "artifacts.get_download_url",
        "artifact_id": str(artifact_id),
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


MD_BYTES = b"# Workflow Notes\n\nReady for review.\n"
MD_TYPE = "text/markdown"


@pytest.mark.asyncio
class TestHttpLocalParity:
    async def test_write_read_list_download_round_trip(self, db_session, storage):
        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        admin_user = _user(user_row.id, org_id=org.id, is_superuser=True)
        principal = _principal(org.id)
        workspace_id = uuid4()

        http_ref = await _http_write(
            db_session,
            user=user,
            filename="Workflow Notes.md",
            content=MD_BYTES,
            content_type=MD_TYPE,
            workspace_id=workspace_id,
        )
        local_write = await _local_write(
            db_session,
            principal=principal,
            filename="Workflow Notes.md",
            content=MD_BYTES,
            content_type=MD_TYPE,
            workspace_id=workspace_id,
        )
        assert local_write["ok"] is True, local_write
        local_ref = local_write["result"]
        assert local_ref["filename"] == http_ref.filename
        assert local_ref["content_type"] == http_ref.content_type
        assert local_ref["size_bytes"] == http_ref.size_bytes
        assert local_ref["type"] == "bifrost_artifact"

        http_bytes = await _http_read(db_session, user=user, artifact_id=UUID(http_ref.id))
        local_read = await _local_read(
            db_session, principal=principal, artifact_id=local_ref["id"]
        )
        assert local_read["ok"] is True, local_read
        assert base64.b64decode(local_read["result"]["content"]) == http_bytes == MD_BYTES
        assert local_read["result"]["content_type"] == MD_TYPE

        http_refs = await _http_list(db_session, user=admin_user, workspace_id=workspace_id)
        local_list = await _local_list(
            db_session, principal=principal, workspace_id=workspace_id
        )
        assert local_list["ok"] is True, local_list
        assert {item["id"] for item in local_list["result"]["items"]} == {
            ref.id for ref in http_refs
        }

        http_url = await _http_download_url(
            db_session, user=admin_user, artifact_id=UUID(local_ref["id"])
        )
        local_url = await _local_download_url(
            db_session, principal=principal, artifact_id=local_ref["id"]
        )
        assert local_url["ok"] is True, local_url
        assert local_url["result"]["url"] == http_url.url
        assert http_url.url.startswith("https://artifacts.local.test/")

    async def test_list_versions_latest_per_logical_path(self, db_session, storage):
        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        admin_user = _user(user_row.id, org_id=org.id, is_superuser=True)
        principal = _principal(org.id)
        workspace_id = uuid4()

        first = await _http_write(
            db_session, user=user, filename="Notes.md", content=b"# v1\n",
            content_type=MD_TYPE, workspace_id=workspace_id,
        )
        second = await _local_write(
            db_session, principal=principal, filename="Notes.md", content=b"# v2\n",
            content_type=MD_TYPE, workspace_id=workspace_id,
        )
        assert second["ok"] is True, second
        assert second["result"]["id"] != first.id

        local_list = await _local_list(
            db_session, principal=principal, workspace_id=workspace_id
        )
        assert local_list["ok"] is True, local_list
        items = local_list["result"]["items"]
        assert [item["filename"] for item in items] == ["Notes.md"]
        assert items[0]["id"] == second["result"]["id"]

        http_bytes = await _http_read(
            db_session, user=admin_user, artifact_id=UUID(second["result"]["id"])
        )
        assert http_bytes == b"# v2\n"

    async def test_read_missing_is_404_both_paths(self, db_session, storage):
        org = await _seed_org(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        principal = _principal(org.id)
        missing = uuid4()

        import fastapi

        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_read(db_session, user=user, artifact_id=missing)
        assert exc_info.value.status_code == 404

        local = await _local_read(db_session, principal=principal, artifact_id=missing)
        assert local["ok"] is False
        assert local["status"] == 404

        url_local = await _local_download_url(
            db_session, principal=principal, artifact_id=missing
        )
        assert url_local["ok"] is False
        assert url_local["status"] == 404

    async def test_out_of_scope_is_404_both_paths(self, db_session, storage):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        user_a = await _seed_user(db_session, org_id=org_a.id)
        user_b = await _seed_user(db_session, org_id=org_b.id)
        workspace_id = uuid4()

        ref = await _http_write(
            db_session,
            user=_user(user_b.id, org_id=org_b.id),
            filename="Private.md",
            content=b"# private\n",
            content_type=MD_TYPE,
            workspace_id=workspace_id,
        )

        import fastapi

        with pytest.raises(fastapi.HTTPException) as exc_info:
            await _http_read(
                db_session,
                user=_user(user_a.id, org_id=org_a.id),
                artifact_id=UUID(ref.id),
            )
        assert exc_info.value.status_code == 404

        local = await _local_read(
            db_session, principal=_principal(org_a.id, is_service=True), artifact_id=ref.id
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_write_validation_is_422_both_paths(self, db_session, storage):
        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        principal = _principal(org.id)

        with pytest.raises(ValueError, match="unsupported artifact type"):
            await _http_write(
                db_session, user=user, filename="payload.bin",
                content=b"data", content_type="application/octet-stream",
            )

        local = await _local_write(
            db_session, principal=principal, filename="payload.bin",
            content=b"data", content_type="application/octet-stream",
        )
        assert local["ok"] is False
        assert local["status"] == 422
        assert "unsupported artifact type" in local["detail"]

        with pytest.raises(ValueError, match="empty"):
            await _http_write(
                db_session, user=user, filename="Empty.md",
                content=b"", content_type=MD_TYPE,
            )
        empty = await _local_write(
            db_session, principal=principal, filename="Empty.md",
            content=b"", content_type=MD_TYPE,
        )
        assert empty["ok"] is False
        assert empty["status"] == 422

    async def test_malformed_ids_are_422(self, db_session, storage):
        principal = _principal(is_platform_admin=True)

        read = await _local_read(db_session, principal=principal, artifact_id="nope")
        assert read["ok"] is False
        assert read["status"] == 422

        url = await _local_download_url(
            db_session, principal=principal, artifact_id="nope"
        )
        assert url["ok"] is False
        assert url["status"] == 422

        from src.services.execution.sdk_local_dispatch import dispatch_frame

        bad_list = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {"v": 1, "id": "bad-ws", "op": "artifacts.list", "workspace_id": "nope"},
        )
        assert bad_list["ok"] is False
        assert bad_list["status"] == 422

        bad_write = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1, "id": "bad-ws-w", "op": "artifacts.write",
                "filename": "Notes.md", "content_type": MD_TYPE,
                "content": base64.b64encode(b"# hi\n").decode("ascii"),
                "workspace_id": "nope",
            },
        )
        assert bad_write["ok"] is False
        assert bad_write["status"] == 422

        bad_b64 = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1, "id": "bad-b64", "op": "artifacts.write",
                "filename": "Notes.md", "content_type": MD_TYPE,
                "content": "!!!not-base64!!!",
            },
        )
        assert bad_b64["ok"] is False
        assert bad_b64["status"] == 422


@pytest.mark.asyncio
class TestServiceOrgBoundary:
    async def test_service_principal_stays_org_scoped(self, db_session, storage):
        from src.services.execution.sdk_local_dispatch import principal_from_context

        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        user_b = await _seed_user(db_session, org_id=org_b.id)
        service_id = str(uuid4())
        workspace_id = uuid4()

        ref = await _http_write(
            db_session,
            user=_user(user_b.id, org_id=org_b.id),
            filename="Org B Notes.md",
            content=b"# org b\n",
            content_type=MD_TYPE,
            workspace_id=workspace_id,
        )

        service_ctx = {
            "execution_id": "attempt-artifact-boundary",
            "caller": {
                "user_id": "00000000-0000-0000-0000-000000000001",
                "email": "initiator@test.local",
                "name": "Initiator",
            },
            "organization": {"id": str(org_a.id)},
            "service": {"service_id": service_id, "attempt_id": "attempt-artifact-boundary"},
        }
        service_principal = principal_from_context(service_ctx)
        assert service_principal.is_service is True
        assert service_principal.caller_org_id == org_a.id

        denied = await _local_read(
            db_session, principal=service_principal, artifact_id=ref.id
        )
        assert denied["ok"] is False
        assert denied["status"] == 404

        denied_url = await _local_download_url(
            db_session, principal=service_principal, artifact_id=ref.id
        )
        assert denied_url["ok"] is False
        assert denied_url["status"] == 404

        denied_list = await _local_list(
            db_session, principal=service_principal, workspace_id=workspace_id
        )
        assert denied_list["ok"] is True, denied_list
        assert denied_list["result"]["items"] == []

        workflow_ctx = {
            "execution_id": "exec-artifact-boundary",
            "caller": {
                "user_id": "initiator",
                "email": "initiator@test.local",
                "name": "Initiator",
            },
            "organization": {"id": str(org_a.id)},
            "is_platform_admin": False,
        }
        workflow_principal = principal_from_context(workflow_ctx)
        allowed = await _local_read(
            db_session, principal=workflow_principal, artifact_id=ref.id
        )
        assert allowed["ok"] is True, allowed
        assert base64.b64decode(allowed["result"]["content"]) == b"# org b\n"

    async def test_local_ignores_child_actor_and_org_claims(self, db_session, storage):
        org = await _seed_org(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        workspace_id = uuid4()
        ref = await _http_write(
            db_session,
            user=_user(user_row.id, org_id=org.id),
            filename="Claimed.md",
            content=b"# claimed\n",
            content_type=MD_TYPE,
            workspace_id=workspace_id,
        )

        from src.services.execution.sdk_local_dispatch import dispatch_frame

        forged = await dispatch_frame(
            lambda: _db_factory(db_session),
            _principal(org.id),
            {
                "v": 1, "id": "forged-actor", "op": "artifacts.read",
                "artifact_id": str(ref.id),
                "actor_email": "attacker@test.local",
                "organization_id": str(uuid4()),
            },
        )
        assert forged["ok"] is True, forged
        assert base64.b64decode(forged["result"]["content"]) == b"# claimed\n"

        other_org = await _seed_org(db_session)
        scoped_out = await dispatch_frame(
            lambda: _db_factory(db_session),
            _principal(other_org.id, is_service=True),
            {
                "v": 1, "id": "forged-org", "op": "artifacts.read",
                "artifact_id": str(ref.id),
                "organization_id": str(org.id),
            },
        )
        assert scoped_out["ok"] is False
        assert scoped_out["status"] == 404


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
    async def test_all_four_ops_round_trip_without_http(self):
        import base64 as _b64

        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        ref = {
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "Workflow Notes.md",
            "content_type": MD_TYPE,
            "size_bytes": len(MD_BYTES),
        }
        handlers = {
            "artifacts.write": lambda f: {
                "v": 1, "id": f["id"], "ok": True, "result": ref,
            },
            "artifacts.read": lambda f: {
                "v": 1, "id": f["id"], "ok": True,
                "result": {
                    "content": _b64.b64encode(MD_BYTES).decode("ascii"),
                    "content_type": MD_TYPE,
                },
            },
            "artifacts.list": lambda f: {
                "v": 1, "id": f["id"], "ok": True, "result": {"items": [ref]},
            },
            "artifacts.get_download_url": lambda f: {
                "v": 1, "id": f["id"], "ok": True,
                "result": {"url": "https://artifacts.local.test/x?sig=test"},
            },
        }
        pump = asyncio.create_task(self._pump_ops(req_recv, resp_send, handlers))
        try:
            from bifrost import artifacts as artifacts_mod
            from bifrost._context import set_execution_context
            from src.sdk.context import ExecutionContext

            ctx = ExecutionContext(
                user_id="u1", email="e@e.com", name="T", scope="org-1",
                organization=None, is_platform_admin=False,
                is_function_key=False, execution_id="exec-1",
                artifact_workspace_id=str(uuid4()),
            )
            set_execution_context(ctx)
            try:
                with patch("bifrost.artifacts.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    written = await artifacts_mod.write(
                        "Workflow Notes.md", MD_BYTES, content_type=MD_TYPE
                    )
                    assert written.id == ref["id"]
                    assert written.size_bytes == len(MD_BYTES)
                    assert await artifacts_mod.read(written) == MD_BYTES
                    listed = await artifacts_mod.list()
                    assert [item.id for item in listed] == [ref["id"]]
                    url = await artifacts_mod.get_download_url(written)
                    assert url == "https://artifacts.local.test/x?sig=test"
            finally:
                from bifrost._context import clear_execution_context

                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_read_dict_ref_and_error_never_falls_back_to_http(self):
        import base64 as _b64

        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAPIError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        handlers = {
            "artifacts.read": lambda f: {
                "v": 1, "id": f["id"], "ok": True,
                "result": {
                    "content": _b64.b64encode(b"raw-bytes").decode("ascii"),
                    "content_type": MD_TYPE,
                },
            },
            "artifacts.get_download_url": lambda f: {
                "v": 1, "id": f["id"], "ok": False,
                "status": 404, "detail": "Artifact not found.",
            },
        }
        pump = asyncio.create_task(self._pump_ops(req_recv, resp_send, handlers))
        try:
            from bifrost import artifacts as artifacts_mod

            with patch("bifrost.artifacts.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                data = await artifacts_mod.read(
                    {
                        "type": "bifrost_artifact",
                        "id": str(uuid4()),
                        "filename": "Notes.md",
                        "content_type": MD_TYPE,
                        "size_bytes": 9,
                    }
                )
                assert data == b"raw-bytes"
                with pytest.raises(BifrostAPIError):
                    await artifacts_mod.get_download_url(
                        {
                            "type": "bifrost_artifact",
                            "id": str(uuid4()),
                            "filename": "Notes.md",
                            "content_type": MD_TYPE,
                            "size_bytes": 9,
                        }
                    )
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_concurrent_calls_share_one_channel(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        count = 20

        async def _pump():
            for _ in range(count):
                raw = await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
                frame = json.loads(raw.decode("utf-8"))
                response = {
                    "v": 1, "id": frame["id"], "ok": True,
                    "result": {"items": []},
                }
                await asyncio.to_thread(resp_send.send_bytes, json.dumps(response).encode())

        pump = asyncio.create_task(_pump())
        try:
            from bifrost import artifacts as artifacts_mod
            from bifrost._context import clear_execution_context, set_execution_context
            from src.sdk.context import ExecutionContext

            ctx = ExecutionContext(
                user_id="u1", email="e@e.com", name="T", scope="org-1",
                organization=None, is_platform_admin=False,
                is_function_key=False, execution_id="exec-1",
                artifact_workspace_id=str(uuid4()),
            )
            set_execution_context(ctx)
            try:
                with patch("bifrost.artifacts.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    results = await asyncio.gather(
                        *[artifacts_mod.list() for _ in range(count)]
                    )
                assert all(items == [] for items in results)
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_timeout_breaks_channel_without_fallback(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(req_send, resp_recv)
        try:
            with pytest.raises(TimeoutError):
                await transport.call_artifacts_list(str(uuid4()), timeout=0.1)
            with pytest.raises(Exception):
                await transport.call_artifacts_list(str(uuid4()), timeout=1.0)
        finally:
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_parent_close_raises_without_fallback(self):
        from bifrost._local_transport import LocalTransportClosed

        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(req_send, resp_recv)
        req_recv.close()
        resp_send.close()
        try:
            with pytest.raises(LocalTransportClosed):
                await transport.call_artifacts_read(str(uuid4()), timeout=5.0)
        finally:
            self._close_all((req_send, resp_recv))

    def test_frame_bounds(self):
        with pytest.raises(Exception):
            encode_frame({"blob": "x" * (MAX_FRAME_BYTES + 1)})
        with pytest.raises(Exception):
            decode_frame(b"not json{")
        with pytest.raises(Exception):
            decode_frame(json.dumps([1, 2]).encode())

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
            assert hasattr(_artifacts_mod, "write")
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


@pytest.mark.asyncio
class TestChunkedTransfer:
    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    async def test_large_read_round_trip_over_pipe(self, db_session, storage):
        """A >64KiB artifact reads locally byte-identical via chunked frames."""
        from bifrost import _local_transport as lt
        from src.services.execution.sdk_local_dispatch import dispatch_frames

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        user_row = await _seed_user(db_session, org_id=org.id)
        user = _user(user_row.id, org_id=org.id)
        principal = _principal(org.id, is_platform_admin=True)
        big = b"# Big\n\n" + b"lorem ipsum dolor sit amet\n" * 5000
        assert len(big) > 100_000
        ref = await _http_write(
            db_session, user=user, filename="Big.md", content=big,
            content_type=MD_TYPE,
        )

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)

        async def _pump():
            raw = await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            request = json.loads(raw.decode("utf-8"))
            frames = await dispatch_frames(
                lambda: _db_factory(db_session), principal, request
            )
            count = 0
            for frame in frames:
                count += 1
                encoded = json.dumps(frame, separators=(",", ":")).encode()
                assert len(encoded) <= MAX_FRAME_BYTES
                await asyncio.to_thread(resp_send.send_bytes, encoded)
            assert count > 1

        pump = asyncio.create_task(_pump())
        try:
            from bifrost import artifacts as artifacts_mod

            with patch("bifrost.artifacts.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                data = await artifacts_mod.read(ref.model_dump(mode="json"))
            assert data == big
            await asyncio.wait_for(pump, timeout=15.0)
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_large_write_round_trip_over_pipe(self, db_session, storage):
        """A >64KiB write rides chunked request frames and persists bytes."""
        from bifrost import _local_transport as lt
        from src.services.execution.sdk_local_dispatch import dispatch_frames

        org = await _seed_org(db_session)
        await _seed_system_user(db_session)
        principal = _principal(org.id)
        workspace_id = uuid4()
        big = b"# Big Write\n\n" + b"row data here\n" * 5000
        assert len(big) > 64 * 1024

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)

        async def _pump():
            from src.services.execution.sdk_local_dispatch import _reassemble_request

            raw = await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            header = json.loads(raw.decode("utf-8"))
            assert header.get("chunked") is True
            request = await _reassemble_request(
                header,
                lambda: asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1),
            )
            frames = await dispatch_frames(
                lambda: _db_factory(db_session), principal, request
            )
            for frame in frames:
                encoded = json.dumps(frame, separators=(",", ":")).encode()
                assert len(encoded) <= MAX_FRAME_BYTES
                await asyncio.to_thread(resp_send.send_bytes, encoded)

        pump = asyncio.create_task(_pump())
        try:
            from bifrost import artifacts as artifacts_mod
            from bifrost._context import clear_execution_context, set_execution_context
            from src.sdk.context import ExecutionContext

            ctx = ExecutionContext(
                user_id="u1", email="e@e.com", name="T", scope="org-1",
                organization=None, is_platform_admin=False,
                is_function_key=False, execution_id="exec-1",
                artifact_workspace_id=str(workspace_id),
            )
            set_execution_context(ctx)
            try:
                with patch("bifrost.artifacts.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    ref = await artifacts_mod.write(
                        "Big Write.md", big, content_type=MD_TYPE
                    )
                assert ref.size_bytes == len(big)
            finally:
                clear_execution_context()
            await asyncio.wait_for(pump, timeout=15.0)
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

        stored = storage.objects
        assert any(content == big for content in stored.values())
