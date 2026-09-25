"""Shared business service for SDK artifact core operations.

Single implementation used by the HTTP handlers
(``api/src/routers/cli.py::sdk_store_artifact``,
``sdk_list_artifacts``, ``sdk_read_artifact``,
``sdk_artifact_download_url``) serving external SDK callers. A future
engine parent-side dispatcher will call the same service with a
parent-derived principal so HTTP and local results are identical by
construction.

Covers the four fixed operations only:

- ``artifacts.write`` (raw workflow-produced bytes)
- ``artifacts.list`` (latest version of every logical file in a workspace)
- ``artifacts.read`` (opaque bytes, optional Office preview, inert headers)
- ``artifacts.get_download_url`` (short-lived signed URL, inert headers)

Generation routes (``document``, ``spreadsheet``, ``text``, ``image``)
live in ``shared.sdk_artifact_generation``; ``video`` and AI routes stay
in the router.

The caller passes an explicit trusted actor (the auth-verified
``UserPrincipal``) plus the DB session — never a FastAPI Request and
never a child-provided actor claim. The HTTP engine token arrives as a
global superuser (``is_superuser=True``, no org); supervised service
tokens arrive with their service org (``is_superuser=False`` plus
``organization_id``). Both flow through unchanged: ``ArtifactService``
applies the platform-admin bypass internally, so the engine sees global
scope while a service token stays org-scoped.

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
A workflow child never imports this module (it stays DB-free behind the
dedicated local channel).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.contracts.artifacts import ArtifactDownloadResponse, ArtifactRef

logger = logging.getLogger(__name__)


class SdkArtifactError(Exception):
    """SDK artifact failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and a future local dispatcher (``ok: false`` frames) can map the
    same failure to their own transport. Currently 404 (missing or
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

    Carries the auth-verified ``UserPrincipal`` and the DB session.
    Built by the HTTP adapter from its authenticated ``CurrentUser``;
    a future parent caller constructs the token-equivalent principal
    from trusted parent metadata. Never derive authority from
    child-supplied fields.
    """

    user: UserPrincipal
    db: AsyncSession

    @classmethod
    def from_context(cls, ctx: Any) -> ArtifactCaller:
        """Build a trusted caller from an authenticated execution context."""
        return cls(user=ctx.user, db=ctx.db)


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
    from src.services.artifacts import ArtifactService, artifact_ref

    validate_artifact_content(
        filename=filename,
        content_type=content_type,
        content=content,
    )
    artifact = await ArtifactService(caller.db).store(
        filename=filename,
        content_type=content_type,
        content=content,
        created_by_user_id=caller.user.user_id,
        organization_id=caller.user.organization_id,
        workspace_id=workspace_id,
        logical_path=filename,
    )
    return artifact_ref(artifact)


async def sdk_list_artifacts(
    caller: ArtifactCaller,
    *,
    workspace_id: UUID,
) -> list[ArtifactRef]:
    """List the latest logical file in one authorized execution workspace.

    Versioning (latest per ``logical_path``) and scope filtering
    (owner-or-org unless platform admin) live in
    ``ArtifactService.list_workspace`` and are preserved unchanged.
    """
    from src.services.artifacts import ArtifactService, artifact_ref

    stored = await ArtifactService(caller.db).list_workspace(
        workspace_id,
        user_id=caller.user.user_id,
        organization_id=caller.user.organization_id,
        is_platform_admin=caller.user.is_platform_admin,
    )
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
            caller's (owner-or-org, admin-bypass) scope.
    """
    from src.services.artifacts import (
        ArtifactAccessError,
        ArtifactService,
        is_browser_active_content_type,
    )

    service = ArtifactService(caller.db)
    try:
        artifact = await service.get_authorized(
            artifact_id,
            user_id=caller.user.user_id,
            organization_id=caller.user.organization_id,
            is_platform_admin=caller.user.is_platform_admin,
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
        artifact = await service.get_authorized(
            artifact_id,
            user_id=caller.user.user_id,
            organization_id=caller.user.organization_id,
            is_platform_admin=caller.user.is_platform_admin,
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
