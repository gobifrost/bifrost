"""Migrated artifact-generation SDK facade tests (Gate C4b).

The generation facade rides the shared ``BifrostClient.engine_request``
transport with the same request bodies and error mapping as the HTTP
endpoints, and never touches a dedicated channel. A >64KiB text body rides
``engine_request`` with no frame ceiling.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


class TestEngineRequestFacade:
    @pytest.mark.asyncio
    async def test_all_four_ops_ride_engine_request_not_channel(self):
        import httpx

        from bifrost import artifacts as artifacts_mod

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

        from bifrost import artifacts as artifacts_mod
        from bifrost.client import BifrostAPIError

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
        assert client.engine_request.await_count == 2

    @pytest.mark.asyncio
    async def test_large_text_round_trip_through_engine_request(self):
        """A >64KiB text body rides engine_request with no frame ceiling."""
        import httpx

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
        client = MagicMock()
        client.engine_request = AsyncMock(return_value=httpx.Response(200, json=ref))
        with patch("bifrost.artifacts.get_client", return_value=client):
            written = await artifacts_mod.create_text(
                "Big.md", format="markdown", content=big_text,
            )
        assert written.size_bytes > 64 * 1024
        assert (
            client.engine_request.await_args.kwargs["json"]["content"] == big_text
        )
