"""Shared business service for SDK artifact generation operations.

Single implementation used by the HTTP handlers
(``api/src/routers/cli.py::sdk_render_document_artifact``,
``sdk_render_spreadsheet_artifact``, ``sdk_render_text_artifact``,
``sdk_generate_image_artifact``) serving external SDK callers. A future
engine parent-side dispatcher will call the same service with a
parent-derived principal so HTTP and local results are identical by
construction.

Covers the four generation operations only:

- ``artifacts.create_document`` (trusted PDF/DOCX rendering + store)
- ``artifacts.create_spreadsheet`` (trusted XLSX rendering + store)
- ``artifacts.create_text`` (trusted text-family rendering + store)
- ``artifacts.create_image`` (provider-backed image generation + store)

Out of scope (unchanged): ``artifacts.write/read/list/get_download_url``
(``shared.sdk_artifacts``), video generation/PlatformJob, AI routes, the
public SDK, and the execution dispatcher.

The caller passes an explicit trusted actor (the auth-verified
``UserPrincipal``) plus the DB session — never a FastAPI Request and
never a child-provided actor claim. Actor/org scope, templates,
rendering, binary validation, provider calls and usage, error
precedence, storage, and metadata match the historical handlers exactly.
Storage behavior is not duplicated here: rendering delegates to
``shared.artifact_generation``, persistence and authorization delegate
to ``src.services.artifacts.ArtifactService``, and image provider calls
plus usage delegate to ``src.services.media_generation``. The HTTP
adapter maps ``SdkArtifactError`` to ``HTTPException`` with the same
status code; ``ValueError`` subclasses (``ArtifactGenerationError``,
``ArtifactAccessError``, ``MediaGenerationError``) propagate unwrapped
so the global ``ValueError → 422`` handler keeps its exact shape.

Transaction semantics (do not silently change):

- ``sdk_render_document_artifact``: DB reads (workspace image resolve +
  S3 reads) happen first, then CPU-bound rendering runs in a worker
  thread holding no DB resources, then a single store flushes. No DB
  session is held across an external provider call.
- ``sdk_render_spreadsheet_artifact`` / ``sdk_render_text_artifact``:
  pure CPU-bound rendering in a worker thread, then a single store.
  No DB session is held across any external call.
- ``sdk_generate_image_artifact``: HOLDS the caller's DB session
  across the external provider HTTP call. ``generate_image`` first
  resolves the provider config with a DB query on the passed session,
  then performs the provider POST on that same session's lifetime,
  and ``record_media_usage`` then writes the AI ledger on the same
  session. Safe split if needed (not applied here): resolve
  ``get_media_provider_config`` in a short-lived session, release it,
  run the provider HTTP with no session held, then re-acquire a
  session for ``ArtifactService.store`` + ``record_media_usage``.

Parent-side only: imports SQLAlchemy sessions and ORM-backed services.
A workflow child never imports this module (it stays DB-free behind the
dedicated local channel).
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from src.models.contracts.artifacts import (
    ArtifactRef,
    DocumentArtifactSpec,
    ImageArtifactSpec,
    SpreadsheetArtifactSpec,
    TextArtifactSpec,
)
from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError

logger = logging.getLogger(__name__)


async def sdk_render_document_artifact(
    caller: ArtifactCaller,
    *,
    spec: DocumentArtifactSpec,
    workspace_id: UUID | None = None,
) -> ArtifactRef:
    """Render and store a trusted PDF or DOCX artifact.

    Workspace image references resolve through the caller's
    (owner-or-org, admin-bypass) scope before rendering; a resolved
    non-image raises transport-neutral 422, and an unresolvable path
    propagates as ``ArtifactAccessError`` (``ValueError`` → 422),
    exactly as the historical handler did.
    """
    from shared.artifact_generation import (
        generate_document,
        generate_document_with_images,
    )
    from src.services.artifacts import ArtifactService, artifact_ref

    service = ArtifactService(caller.db)
    image_content: dict[str, bytes] = {}
    if workspace_id is not None:
        for image in (
            image for section in spec.sections for image in section.images
        ):
            stored_image = await service.resolve_workspace_path(
                workspace_id,
                image.path,
                user_id=caller.user.user_id,
                organization_id=caller.user.organization_id,
                is_platform_admin=caller.user.is_platform_admin,
            )
            if not stored_image.content_type.startswith("image/"):
                raise SdkArtifactError(
                    422, f"{image.path} is not an image artifact."
                )
            image_content[image.path] = await service.read(stored_image)
    generated = await asyncio.to_thread(
        generate_document_with_images if image_content else generate_document,
        spec,
        *([image_content] if image_content else []),
    )
    artifact = await service.store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=caller.user.user_id,
        organization_id=caller.user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    return artifact_ref(artifact)


async def sdk_render_spreadsheet_artifact(
    caller: ArtifactCaller,
    *,
    spec: SpreadsheetArtifactSpec,
    workspace_id: UUID | None = None,
) -> ArtifactRef:
    """Render and store a trusted XLSX artifact."""
    from shared.artifact_generation import generate_spreadsheet
    from src.services.artifacts import ArtifactService, artifact_ref

    generated = await asyncio.to_thread(generate_spreadsheet, spec)
    artifact = await ArtifactService(caller.db).store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=caller.user.user_id,
        organization_id=caller.user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    return artifact_ref(artifact)


async def sdk_render_text_artifact(
    caller: ArtifactCaller,
    *,
    spec: TextArtifactSpec,
    workspace_id: UUID | None = None,
) -> ArtifactRef:
    """Render and store a trusted text-family artifact."""
    from shared.artifact_generation import generate_text
    from src.services.artifacts import ArtifactService, artifact_ref

    generated = await asyncio.to_thread(generate_text, spec)
    artifact = await ArtifactService(caller.db).store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=caller.user.user_id,
        organization_id=caller.user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    return artifact_ref(artifact)


async def sdk_generate_image_artifact(
    caller: ArtifactCaller,
    *,
    spec: ImageArtifactSpec,
    workspace_id: UUID | None = None,
    execution_id: UUID | None = None,
) -> ArtifactRef:
    """Generate and store an image with the configured provider.

    NOTE: holds the caller's DB session across the external provider
    HTTP call (see module docstring for the safe split proposal).
    Semantics are preserved unchanged from the historical handler.
    """
    from src.services.artifacts import ArtifactService, artifact_ref
    from src.services.media_generation import generate_image, record_media_usage

    generated = await generate_image(
        caller.db,
        filename=spec.filename,
        prompt=spec.prompt,
    )
    artifact = await ArtifactService(caller.db).store(
        filename=generated.filename,
        content_type=generated.content_type,
        content=generated.content,
        created_by_user_id=caller.user.user_id,
        organization_id=caller.user.organization_id,
        workspace_id=workspace_id,
        logical_path=generated.filename,
    )
    await record_media_usage(
        caller.db,
        generated,
        execution_id=execution_id,
        organization_id=caller.user.organization_id,
        user_id=caller.user.user_id,
    )
    return artifact_ref(artifact)


__all__ = [
    "sdk_generate_image_artifact",
    "sdk_render_document_artifact",
    "sdk_render_spreadsheet_artifact",
    "sdk_render_text_artifact",
]
