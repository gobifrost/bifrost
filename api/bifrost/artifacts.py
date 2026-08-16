"""Schema-first artifact generation for Bifrost workflows.

Rendering and persistence run on the platform with the same trusted generators
used by Chat. Every operation returns one opaque ``ArtifactRef`` that workflows,
Chat, and MCP can pass without exposing storage coordinates.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from ._context import _execution_context
from .client import get_client, raise_for_status_with_detail
from .models import ArtifactRef


def _workspace_params() -> dict[str, str]:
    """Attach the active run workspace without exposing storage coordinates."""
    context = _execution_context.get()
    if context is None:
        return {}
    workspace_id = context.artifact_workspace_id or context.execution_id
    return {
        "workspace_id": str(workspace_id),
        "execution_id": str(context.execution_id),
    }


class artifacts:
    """Create, read, and share generated files from workflows."""

    @staticmethod
    async def _render(
        endpoint: str,
        payload: dict[str, Any],
    ) -> ArtifactRef:
        client = get_client()
        response = await client.post(endpoint, json=payload, params=_workspace_params())
        raise_for_status_with_detail(response)
        return ArtifactRef.model_validate(response.json())

    @staticmethod
    async def write(
        filename: str,
        content: bytes,
        *,
        content_type: str,
    ) -> ArtifactRef:
        """Store validated workflow-produced bytes behind an opaque reference."""
        response = await get_client().post(
            "/api/sdk/artifacts",
            files={"file": (filename, content, content_type)},
            params=_workspace_params(),
        )
        raise_for_status_with_detail(response)
        return ArtifactRef.model_validate(response.json())

    @staticmethod
    async def create_document(
        filename: str,
        *,
        format: Literal["pdf", "docx"],
        title: str,
        sections: list[dict[str, Any]],
        subtitle: str | None = None,
        page_size: Literal["letter", "a4"] = "letter",
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
        )

    @staticmethod
    async def create_spreadsheet(
        filename: str,
        *,
        sheets: list[dict[str, Any]],
    ) -> ArtifactRef:
        """Create a styled XLSX workbook and return its reference."""
        return await artifacts._render(
            "/api/sdk/artifacts/spreadsheet",
            {"filename": filename, "sheets": sheets},
        )

    @staticmethod
    async def create_text(
        filename: str,
        *,
        format: Literal["csv", "html", "markdown", "text", "json"],
        content: str,
    ) -> ArtifactRef:
        """Create a text-family artifact and return its reference."""
        return await artifacts._render(
            "/api/sdk/artifacts/text",
            {"filename": filename, "format": format, "content": content},
        )

    @staticmethod
    async def create_image(
        filename: str,
        *,
        prompt: str,
    ) -> ArtifactRef:
        """Generate an image with the configured image model."""
        return await artifacts._render(
            "/api/sdk/artifacts/image",
            {"filename": filename, "prompt": prompt},
        )

    @staticmethod
    async def create_video(
        filename: str,
        *,
        prompt: str,
        timeout_seconds: float = 1_800,
        poll_interval_seconds: float = 2,
    ) -> ArtifactRef:
        """Generate a video through a durable platform job and return its reference."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero.")
        client = get_client()
        response = await client.post(
            "/api/sdk/artifacts/video",
            json={
                "filename": filename,
                "prompt": prompt,
            },
            params=_workspace_params(),
        )
        raise_for_status_with_detail(response)
        job_id = str(response.json()["job_id"])
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status_response = await client.get(f"/api/platform-jobs/{job_id}")
            raise_for_status_with_detail(status_response)
            job = status_response.json()
            status = str(job.get("status") or "")
            if status == "succeeded":
                result = job.get("result")
                artifact = result.get("artifact") if isinstance(result, dict) else None
                if not isinstance(artifact, dict):
                    raise RuntimeError("Video generation completed without an artifact.")
                return ArtifactRef.model_validate(artifact)
            if status in {"failed", "cancelled"}:
                error = job.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                raise RuntimeError(message or f"Video generation {status}.")
            await asyncio.sleep(poll_interval_seconds)
        raise TimeoutError(
            f"Video generation is still running as platform job {job_id}."
        )

    @staticmethod
    async def read(ref: ArtifactRef | dict[str, Any]) -> bytes:
        """Read an ArtifactRef received as workflow or MCP tool input."""
        artifact = ref if isinstance(ref, ArtifactRef) else ArtifactRef.model_validate(ref)
        response = await get_client().get(f"/api/sdk/artifacts/{artifact.id}/content")
        raise_for_status_with_detail(response)
        return response.content

    @staticmethod
    async def list() -> list[ArtifactRef]:
        """List the latest files available in the active run workspace."""
        params = _workspace_params()
        if not params:
            raise RuntimeError(
                "artifacts.list() requires an active workflow or agent execution."
            )
        response = await get_client().get("/api/sdk/artifacts", params=params)
        raise_for_status_with_detail(response)
        return [ArtifactRef.model_validate(item) for item in response.json()]

    @staticmethod
    async def get_download_url(ref: ArtifactRef | dict[str, Any]) -> str:
        """Create a short-lived download URL for an authorized ArtifactRef."""
        artifact = ref if isinstance(ref, ArtifactRef) else ArtifactRef.model_validate(ref)
        response = await get_client().get(
            f"/api/sdk/artifacts/{artifact.id}/download-url"
        )
        raise_for_status_with_detail(response)
        return str(response.json()["url"])
