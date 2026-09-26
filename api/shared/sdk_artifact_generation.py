"""Shared business service for SDK artifact generation operations.

Single implementation used by the HTTP handlers
(``api/src/routers/cli.py::sdk_render_document_artifact``,
``sdk_render_spreadsheet_artifact``, ``sdk_render_text_artifact``,
``sdk_generate_image_artifact``) serving external SDK callers and
reached by workflow children over the worker-local engine socket with a
parent-derived principal, so results are identical by construction.

Covers the four generation operations only:

- ``artifacts.create_document`` (trusted PDF/DOCX rendering + store)
- ``artifacts.create_spreadsheet`` (trusted XLSX rendering + store)
- ``artifacts.create_text`` (trusted text-family rendering + store)
- ``artifacts.create_image`` (provider-backed image generation + store)

Out of scope (unchanged): ``artifacts.write/read/list/get_download_url``
(``shared.sdk_artifacts``), video generation/PlatformJob, AI routes, the
public SDK, and the worker-local engine socket.

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

- ``sdk_render_document_artifact``: workspace image reads (DB resolve +
  S3 reads) happen first in a short-lived session derived from the
  caller's bind, its connection is released, then CPU-bound rendering
  runs in a worker thread holding no DB resources, then a single store
  on the caller's session flushes. No DB session is held across
  rendering.
- ``sdk_render_spreadsheet_artifact`` / ``sdk_render_text_artifact``:
  pure CPU-bound rendering in a worker thread, then a single store.
  No DB session is checked out before rendering; the caller's session
  is first touched by the final store. No DB session is held across
  any external call.
- ``sdk_generate_image_artifact``: resolves ``get_media_provider_config``
  in a short-lived session derived from the caller's bind, releases
  it, runs the provider HTTP with no session held via
  ``generate_image_with_config``, then stores and records usage on the
  caller's session. Store + usage share the caller's transaction until
  the HTTP dependency commits.

Parent-side only: imports SQLAlchemy sessions and ORM-backed services.
A workflow child never imports this module (it stays DB-free and reaches
it over the engine socket).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from src.models.contracts.artifacts import (
    ArtifactRef,
    DocumentArtifactSpec,
    ImageArtifactSpec,
    SpreadsheetArtifactSpec,
    TextArtifactSpec,
)
from shared.sdk_artifacts import ArtifactCaller, SdkArtifactError
from sqlalchemy.ext.asyncio import AsyncSession

def _isolated_session(caller_db: AsyncSession) -> AsyncSession:
    """Open a short-lived session on the caller's engine.

    Reads (provider config, workspace image resolve) run here so the
    caller's session checks out its first connection only at the final
    store. The ``async with`` close releases the connection before the
    long provider HTTP / CPU rendering step. Same engine/pool — no new
    pool, no retry, no fallback.
    """
    return AsyncSession(
        caller_db.bind, expire_on_commit=False, autoflush=False
    )


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

    image_content: dict[str, bytes] = {}
    if workspace_id is not None and any(section.images for section in spec.sections):
        async with _isolated_session(caller.db) as read_db:
            read_service = ArtifactService(read_db)
            for image in (
                image for section in spec.sections for image in section.images
            ):
                stored_image = await read_service.resolve_workspace_path(
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
                image_content[image.path] = await read_service.read(stored_image)
    generated = await asyncio.to_thread(
        generate_document_with_images if image_content else generate_document,
        spec,
        *([image_content] if image_content else []),
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

    Resolves the provider config in a short-lived session, releases its
    connection, runs the provider HTTP with no DB session held, then
    stores and records usage on the caller's session in one transaction.
    Provider request, response, error, model, usage, and artifact
    behavior match the historical handler exactly.
    """
    from src.services.artifacts import ArtifactService, artifact_ref
    from src.services.media_generation import (
        generate_image_with_config,
        get_media_provider_config,
        record_media_usage,
    )

    async with _isolated_session(caller.db) as config_db:
        config = await get_media_provider_config(config_db, "image")
    generated = await generate_image_with_config(
        config,
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
