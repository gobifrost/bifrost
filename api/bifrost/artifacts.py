"""Schema-first artifact generation for Bifrost workflows.

Rendering runs on the platform with the same trusted generators used by Chat.
The SDK then persists the bytes through the existing managed-files surface and
returns a portable ``ArtifactRef`` that tools can accept or return as JSON.
"""

from __future__ import annotations

import base64
from typing import Any, Literal
from uuid import uuid4

from ._context import resolve_scope
from .client import get_client, raise_for_status_with_detail
from .files import files
from .models import ArtifactRef


class artifacts:
    """Create, read, and share generated files from workflows."""

    @staticmethod
    async def _render(
        endpoint: str,
        payload: dict[str, Any],
        *,
        location: str,
        scope: str | None,
    ) -> ArtifactRef:
        client = get_client()
        response = await client.post(endpoint, json=payload)
        raise_for_status_with_detail(response)
        rendered = response.json()
        content = base64.b64decode(rendered["content_base64"], validate=True)
        filename = str(rendered["filename"])
        path = f"artifacts/{uuid4()}/{filename}"
        effective_scope = resolve_scope(scope)
        await files.write_bytes(
            path,
            content,
            location=location,
            scope=effective_scope,
            create_only=True,
        )
        return ArtifactRef(
            filename=filename,
            content_type=str(rendered["content_type"]),
            size_bytes=len(content),
            path=path,
            location=location,
            scope=effective_scope,
        )

    @staticmethod
    async def create_document(
        filename: str,
        *,
        format: Literal["pdf", "docx"],
        title: str,
        sections: list[dict[str, Any]],
        subtitle: str | None = None,
        page_size: Literal["letter", "a4"] = "letter",
        location: str = "temp",
        scope: str | None = None,
    ) -> ArtifactRef:
        """Create a flowing PDF or DOCX document and return its reference."""
        return await artifacts._render(
            "/api/sdk/artifacts/document",
            {
                "filename": filename,
                "format": format,
                "title": title,
                "subtitle": subtitle,
                "sections": sections,
                "page_size": page_size,
            },
            location=location,
            scope=scope,
        )

    @staticmethod
    async def create_spreadsheet(
        filename: str,
        *,
        sheets: list[dict[str, Any]],
        location: str = "temp",
        scope: str | None = None,
    ) -> ArtifactRef:
        """Create a styled XLSX workbook and return its reference."""
        return await artifacts._render(
            "/api/sdk/artifacts/spreadsheet",
            {"filename": filename, "sheets": sheets},
            location=location,
            scope=scope,
        )

    @staticmethod
    async def create_text(
        filename: str,
        *,
        format: Literal["csv", "html", "markdown", "text", "json"],
        content: str,
        location: str = "temp",
        scope: str | None = None,
    ) -> ArtifactRef:
        """Create a text-family artifact and return its reference."""
        return await artifacts._render(
            "/api/sdk/artifacts/text",
            {"filename": filename, "format": format, "content": content},
            location=location,
            scope=scope,
        )

    @staticmethod
    async def read(ref: ArtifactRef | dict[str, Any]) -> bytes:
        """Read an ArtifactRef received as workflow or MCP tool input."""
        artifact = ref if isinstance(ref, ArtifactRef) else ArtifactRef.model_validate(ref)
        if not artifact.path or not artifact.location:
            raise ValueError("This artifact reference does not contain a managed-file path.")
        return await files.read_bytes(
            artifact.path,
            location=artifact.location,
            scope=artifact.scope,
        )

    @staticmethod
    async def get_download_url(ref: ArtifactRef | dict[str, Any]) -> str:
        """Create a short-lived download URL for a managed ArtifactRef."""
        artifact = ref if isinstance(ref, ArtifactRef) else ArtifactRef.model_validate(ref)
        if not artifact.path or not artifact.location:
            raise ValueError("This artifact reference does not contain a managed-file path.")
        signed = await files.get_signed_url(
            artifact.path,
            method="GET",
            content_type=artifact.content_type,
            location=artifact.location,
            scope=artifact.scope,
        )
        return str(signed["url"])
