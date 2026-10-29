"""Shared business service for SDK artifact core operations.

Single implementation used by the HTTP handlers
(``api/src/routers/cli.py::sdk_store_artifact``,
``sdk_list_artifacts``, ``sdk_read_artifact``,
``sdk_artifact_download_url``) serving external SDK callers and reached
by workflow children over the worker-local engine socket with a
parent-derived principal, so results are identical by construction.

Covers the four fixed operations only:

- ``artifacts.write`` (raw workflow-produced bytes)
- ``artifacts.list`` (latest version of every logical file in a workspace)
- ``artifacts.read`` (opaque bytes, optional Office preview, inert headers)
- ``artifacts.get_download_url`` (short-lived signed URL, inert headers)

Generation routes (``document``, ``spreadsheet``, ``text``, ``image``)
live in ``shared.sdk_artifact_generation``; ``video`` and AI routes stay
in the router.

The caller passes an explicit, verified :class:`Caller` plus the DB session
— never a FastAPI Request or child-provided actor claim. ``ArtifactService``
refreshes that caller at each private-artifact boundary, authorizes with the
original user's current reach and permissions, and records the effects actor
on new rows.

Storage behavior is not duplicated here: validation delegates to
``shared.artifact_generation.validate_artifact_content``, persistence
and authorization delegate to ``src.services.artifacts.ArtifactService``
(workspace versioning, inert storage, signed-URL disposition). All
failures are transport-neutral: authorization misses raise
:class:`SdkArtifactError` (404, matching the historical handler);
content-validation failures propagate as ``ValueError``
(``ArtifactGenerationError``) so the global ``ValueError → 422``
handler keeps its exact shape. The HTTP adapter maps
``SdkArtifactError`` to ``HTTPException`` and constructs
``Response`` objects from the returned results.

Parent-side only: imports SQLAlchemy sessions and ORM-backed services.
A workflow child never imports this module (it stays DB-free and reaches
it over the engine socket).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.artifacts import ArtifactDownloadResponse, ArtifactRef
from src.services.authorization.enforce import Caller


class SdkArtifactError(Exception):
    """SDK artifact failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Currently 404 (missing or
    out-of-scope artifact, matching the historical handler responses
    exactly) and 422 (document image path resolving to a non-image,
    matching the historical handler's explicit 422). Validation failures are ``ValueError`` and propagate
    unwrapped to preserve the global ``ValueError → 422`` shape.
    """

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass
class ArtifactCaller:
    """Trusted caller for SDK artifact operations.

    Carries the verified original-user authority and DB session.  The effects
    principal remains on ``caller.principal`` for persisted attribution.
    """

    caller: Caller
    db: AsyncSession


@dataclass
class ArtifactReadResult:
    """Transport-neutral read result — the adapter builds the Response.

    ``preview_html`` is set only when ``preview=True`` was requested and
    the stored bytes are a previewable Office type (DOCX/XLSX); the
    adapter serves it as ``text/html`` with the locked-down CSP headers.
    Otherwise ``content``/``content_type`` are the raw stored bytes and
    ``content_disposition`` is ``"attachment"`` for browser-active types
    (HTML/SVG/XML) or ``None``.
    """

    content: bytes
    content_type: str
    content_disposition: str | None = None
    preview_html: str | None = None


async def sdk_store_artifact(
    caller: ArtifactCaller,
    *,
    filename: str,
    content_type: str,
    content: bytes,
    workspace_id: UUID | None = None,
) -> ArtifactRef:
    """Validate and store workflow-produced bytes behind an opaque identity.

    Validation runs first (``ArtifactGenerationError`` on empty,
    over-limit, signature-mismatch, or unsupported types — propagates as
    ``ValueError`` → 422). Ownership uses the trusted actor's user id
    and org; the logical path defaults to the filename so workspace
    versioning keys on it, exactly as the historical handler did.
    """
    from shared.artifact_generation import validate_artifact_content
    from src.services.artifacts import ArtifactAccessError, ArtifactService, artifact_ref

    validate_artifact_content(
        filename=filename,
        content_type=content_type,
        content=content,
    )
    try:
        artifact = await ArtifactService(caller.db).store_for_caller(
            caller.caller,
            filename=filename,
            content_type=content_type,
            content=content,
            workspace_id=workspace_id,
            logical_path=filename,
        )
    except ArtifactAccessError as exc:
        raise SdkArtifactError(404, str(exc)) from exc
    return artifact_ref(artifact)


async def sdk_list_artifacts(
    caller: ArtifactCaller,
    *,
    workspace_id: UUID,
) -> list[ArtifactRef]:
    """List the latest logical file in one authorized execution workspace.

    Versioning (latest per ``logical_path``) lives in
    ``ArtifactService.list_workspace_for_caller`` applies original-user reach
    and owner-or-named-power filtering for every persisted row.
    """
    from src.services.artifacts import ArtifactAccessError, ArtifactService, artifact_ref

    try:
        stored = await ArtifactService(caller.db).list_workspace_for_caller(
            workspace_id,
            caller=caller.caller,
        )
    except ArtifactAccessError as exc:
        raise SdkArtifactError(404, str(exc)) from exc
    return [artifact_ref(item) for item in stored]


async def sdk_read_artifact(
    caller: ArtifactCaller,
    *,
    artifact_id: UUID,
    preview: bool = False,
) -> ArtifactReadResult:
    """Read an opaque artifact after enforcing caller scope.

    Raises:
        SdkArtifactError: 404 when the id is missing or outside the
            caller's original-user owner-or-named-power scope.
    """
    from src.services.artifacts import (
        ArtifactAccessError,
        ArtifactService,
        is_browser_active_content_type,
    )

    service = ArtifactService(caller.db)
    try:
        artifact = await service.get_for_caller(
            artifact_id,
            caller=caller.caller,
        )
    except ArtifactAccessError as exc:
        raise SdkArtifactError(404, str(exc)) from exc
    content = await service.read(artifact)
    if preview:
        from shared.artifact_preview import preview_office_artifact

        preview_html = await asyncio.to_thread(
            preview_office_artifact,
            content,
            artifact.content_type,
        )
        if preview_html is not None:
            return ArtifactReadResult(
                content=content,
                content_type=artifact.content_type,
                preview_html=preview_html,
            )
    disposition = (
        "attachment"
        if is_browser_active_content_type(artifact.content_type)
        else None
    )
    return ArtifactReadResult(
        content=content,
        content_type=artifact.content_type,
        content_disposition=disposition,
    )


async def sdk_artifact_download_url(
    caller: ArtifactCaller,
    *,
    artifact_id: UUID,
) -> ArtifactDownloadResponse:
    """Create a short-lived download URL for an opaque artifact.

    The signed URL keeps browser-active artifacts inert
    (``response-content-type=application/octet-stream`` plus
    ``response-content-disposition=attachment``) via
    ``ArtifactService.generate_download_url``.

    Raises:
        SdkArtifactError: 404 when the id is missing or outside the
            caller's scope.
    """
    from src.services.artifacts import ArtifactAccessError, ArtifactService

    service = ArtifactService(caller.db)
    try:
        artifact = await service.get_for_caller(
            artifact_id,
            caller=caller.caller,
        )
    except ArtifactAccessError as exc:
        raise SdkArtifactError(404, str(exc)) from exc
    url = await service.generate_download_url(artifact)
    return ArtifactDownloadResponse(url=url)


__all__ = [
    "ArtifactCaller",
    "ArtifactReadResult",
    "SdkArtifactError",
    "sdk_artifact_download_url",
    "sdk_list_artifacts",
    "sdk_read_artifact",
    "sdk_store_artifact",
]
