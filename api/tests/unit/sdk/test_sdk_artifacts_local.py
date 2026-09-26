"""Migrated artifact SDK facade tests (Gate C4b).

The artifact facade rides the shared ``BifrostClient.engine_request``
transport and never touches a dedicated channel: paths, bodies, response
parsing, and error mapping stay identical to the HTTP endpoints, with no
silent HTTP fallback. Large payloads ride ``engine_request`` with no frame
ceilings.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

MD_BYTES = b"# Workflow Notes\n\nReady for review.\n"
MD_TYPE = "text/markdown"


class TestEngineRequestFacade:
    @pytest.mark.asyncio
    async def test_all_four_ops_ride_engine_request_not_channel(self):
        import httpx

        from bifrost import artifacts as artifacts_mod

        ref = {
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "Workflow Notes.md",
            "content_type": MD_TYPE,
            "size_bytes": len(MD_BYTES),
        }
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=[
            httpx.Response(200, json=ref),
            httpx.Response(200, content=MD_BYTES),
            httpx.Response(200, json=[ref]),
            httpx.Response(
                200, json={"url": "https://artifacts.local.test/x?sig=test"}
            ),
        ])
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

        # The migrated facade never reads a channel frame: every call rode the
        # shared client's engine-local entry point with the HTTP path/body.
        calls = client.engine_request.await_args_list
        assert [(call.args[0], call.args[1]) for call in calls] == [
            ("POST", "/api/sdk/artifacts"),
            ("GET", f"/api/sdk/artifacts/{ref['id']}/content"),
            ("GET", "/api/sdk/artifacts"),
            ("GET", f"/api/sdk/artifacts/{ref['id']}/download-url"),
        ]
        assert calls[0].kwargs["params"]["workspace_id"] == str(workspace_id)

    @pytest.mark.asyncio
    async def test_read_dict_ref_and_error_surface_without_channel(self):
        import httpx

        from bifrost import artifacts as artifacts_mod
        from bifrost.client import BifrostAPIError

        ref = {
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "Notes.md",
            "content_type": MD_TYPE,
            "size_bytes": 9,
        }
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=[
            httpx.Response(200, content=b"raw-bytes"),
            httpx.Response(
                404,
                json={"detail": "Artifact not found."},
                request=httpx.Request(
                    "GET", "http://api/api/sdk/artifacts/x/download-url"
                ),
            ),
        ])
        with patch("bifrost.artifacts.get_client", return_value=client):
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
                await artifacts_mod.get_download_url(ref)
        assert client.engine_request.await_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_calls_share_the_engine_request_client(self):
        import httpx

        from bifrost import artifacts as artifacts_mod

        count = 20
        client = MagicMock()
        client.engine_request = AsyncMock(
            side_effect=[httpx.Response(200, json=[]) for _ in range(count)]
        )
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
            with patch("bifrost.artifacts.get_client", return_value=client):
                results = await asyncio.gather(
                    *[artifacts_mod.list() for _ in range(count)]
                )
            assert all(items == [] for items in results)
        finally:
            clear_execution_context()
        assert client.engine_request.await_count == count


class TestChunkedTransfer:
    """Large artifact payloads ride engine_request without frame ceilings."""

    def _client(self, responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_large_bytes_round_trip_through_engine_request(self):
        import httpx

        from bifrost import artifacts as artifacts_mod

        big = b"# Big\n\n" + b"lorem ipsum dolor sit amet\n" * 5000
        assert len(big) > 100_000
        ref = {
            "type": "bifrost_artifact",
            "id": str(uuid4()),
            "filename": "Big.md",
            "content_type": MD_TYPE,
            "size_bytes": len(big),
        }
        client = self._client([
            httpx.Response(200, json=ref),
            httpx.Response(200, content=big),
        ])
        with patch("bifrost.artifacts.get_client", return_value=client):
            written = await artifacts_mod.write(
                "Big.md", big, content_type=MD_TYPE
            )
            data = await artifacts_mod.read(written)
        assert data == big
        assert written.size_bytes == len(big)
        assert (
            client.engine_request.await_args_list[0].kwargs["files"]["file"][1]
            == big
        )
