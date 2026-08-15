from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcp.types import ImageContent, ResourceLink

from src.services.mcp_server import server


@pytest.mark.asyncio
async def test_workflow_artifact_results_become_mcp_media_and_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid4()
    context = server.MCPContext(user_id=uuid4(), org_id=org_id)
    monkeypatch.setattr(server, "_get_context_from_token", lambda: context)
    monkeypatch.setattr(
        server,
        "_execute_workflow_tool_impl",
        AsyncMock(
            return_value={
                "artifacts": [
                    {
                        "type": "bifrost_artifact",
                        "filename": "chart.png",
                        "content_type": "image/png",
                        "size_bytes": 8,
                        "path": "artifacts/chart.png",
                        "location": "temp",
                        "scope": str(org_id),
                    },
                    {
                        "type": "bifrost_artifact",
                        "filename": "brief.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 12,
                        "path": "artifacts/brief.pdf",
                        "location": "temp",
                        "scope": str(org_id),
                    },
                ]
            }
        ),
    )

    @asynccontextmanager
    async def fake_db_context():
        yield object()

    storage = AsyncMock()
    storage.read_uploaded_file.return_value = b"png-data"
    storage.generate_presigned_download_url.return_value = (
        "https://files.example.test/brief.pdf"
    )
    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.file_storage.service.get_file_storage_service",
        lambda db: storage,
    )

    tool = server.WorkflowTool(
        name="create_brief",
        description="Create files",
        workflow_id=str(uuid4()),
        workflow_name="Create Brief",
        parameters={"type": "object", "properties": {}},
    )
    result = await tool.run({})

    assert result.structured_content is not None
    assert result.structured_content["artifacts"][0]["filename"] == "chart.png"
    assert any(isinstance(block, ImageContent) for block in result.content)
    resource = next(
        block for block in result.content if isinstance(block, ResourceLink)
    )
    assert resource.name == "brief.pdf"
    assert str(resource.uri) == "https://files.example.test/brief.pdf"
