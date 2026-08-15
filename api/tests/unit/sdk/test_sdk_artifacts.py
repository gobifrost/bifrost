import base64
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_create_document_renders_then_persists_managed_file(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")

    response = MagicMock()
    response.json.return_value = {
        "filename": "brief.pdf",
        "content_type": "application/pdf",
        "size_bytes": 8,
        "content_base64": base64.b64encode(b"%PDF-ok").decode("ascii"),
    }
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())
    monkeypatch.setattr(module, "resolve_scope", lambda scope: scope or "org-1")
    write = AsyncMock()
    monkeypatch.setattr(module.files, "write_bytes", write)

    ref = await module.artifacts.create_document(
        "brief",
        format="pdf",
        title="Brief",
        sections=[{"heading": "Summary", "paragraphs": ["Ready"]}],
    )

    assert ref.type == "bifrost_artifact"
    assert ref.filename == "brief.pdf"
    assert ref.location == "temp"
    assert ref.scope == "org-1"
    assert ref.path is not None and ref.path.endswith("/brief.pdf")
    write.assert_awaited_once_with(
        ref.path,
        b"%PDF-ok",
        location="temp",
        scope="org-1",
        create_only=True,
    )


@pytest.mark.asyncio
async def test_read_accepts_json_artifact_reference(monkeypatch) -> None:
    module = importlib.import_module("bifrost.artifacts")

    read = AsyncMock(return_value=b"workbook")
    monkeypatch.setattr(module.files, "read_bytes", read)

    data = await module.artifacts.read(
        {
            "type": "bifrost_artifact",
            "filename": "report.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size_bytes": 8,
            "path": "artifacts/id/report.xlsx",
            "location": "temp",
            "scope": "org-1",
        }
    )

    assert data == b"workbook"
    read.assert_awaited_once_with(
        "artifacts/id/report.xlsx", location="temp", scope="org-1"
    )
