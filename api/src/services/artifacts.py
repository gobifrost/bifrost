"""Canonical artifact persistence and opaque-reference resolution."""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.artifacts import ArtifactRef
from src.models.orm import Artifact
from src.services.authorization.enforce import (
    Caller,
    PermissionDenied,
    require_named_permission,
    user_reach,
)
from src.services.file_storage.service import get_file_storage_service


_BROWSER_ACTIVE_CONTENT_TYPES = {
    "application/xhtml+xml",
    "application/xml",
    "image/svg+xml",
    "text/html",
    "text/xml",
}


def is_browser_active_content_type(content_type: str) -> bool:
    """Return whether a media type can render active browser content."""
    base_content_type = content_type.partition(";")[0].strip().lower()
    return (
        base_content_type in _BROWSER_ACTIVE_CONTENT_TYPES
        or base_content_type.endswith("+xml")
    )


def artifact_requires_inert_storage(filename: str, content_type: str) -> bool:
    """Return whether declared or filename-derived metadata is browser-active."""
    inferred_content_type, _encoding = mimetypes.guess_type(filename)
    return is_browser_active_content_type(content_type) or (
        inferred_content_type is not None
        and is_browser_active_content_type(inferred_content_type)
    )


class ArtifactAccessError(ValueError):
    """Raised when an artifact reference is missing or outside the caller's scope."""


async def refresh_artifact_caller(db: AsyncSession, caller: Caller) -> Caller:
    """Reload current authority before each private-artifact decision.

    Typed runtime credentials are durable records, so their original role
    context and workflow powers must be resolved again for every operation.
    Ordinary callers also reload their current authorization context.  A
    caller without an original-user context cannot use the private-artifact
    authority path.
    """
    from src.services.authorization.callers import CallerUnavailable, refresh_caller

    try:
        return await refresh_caller(db, caller)
    except CallerUnavailable:
        raise ArtifactAccessError("Artifact not found.")


def artifact_ref(artifact: Artifact) -> ArtifactRef:
    """Return the only public identity exposed for a stored artifact."""
    return ArtifactRef(
        id=str(artifact.id),
        filename=artifact.filename,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
    )


def normalize_artifact_path(path: str) -> str:
    """Normalize a user-facing workspace path without exposing S3 keys."""
    normalized = path.replace("\\", "/").strip().lstrip("/")
    parts = PurePosixPath(normalized).parts
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Artifact workspace path is invalid.")
    if len(normalized) > 500:
        raise ValueError("Artifact workspace path is too long.")
    return normalized


class ArtifactService:
    """Store and resolve files without exposing backend storage coordinates."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _store_persist(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        created_by_user_id: UUID,
        organization_id: UUID | None,
        storage_family: str = "generated",
        workspace_id: UUID | None = None,
        logical_path: str | None = None,
    ) -> Artifact:
        if not filename:
            raise ValueError("Artifact filename is required.")
        if not content:
            raise ValueError(f"{filename} is empty.")
        artifact_id = uuid4()
        safe_name = filename.replace("/", "_").replace("\\", "_")
        resolved_path = (
            normalize_artifact_path(logical_path or filename)
            if workspace_id is not None
            else None
        )
        if workspace_id is not None:
            s3_key = f"_artifact_workspaces/{workspace_id}/{artifact_id}/{safe_name}"
        else:
            prefix = "_attachments" if storage_family == "upload" else "_artifacts"
            s3_key = f"{prefix}/{artifact_id}_{safe_name}"
        storage = get_file_storage_service(self.db)
        if artifact_requires_inert_storage(filename, content_type):
            async def content_chunks():
                yield content

            await storage.write_raw_chunks_to_s3(
                s3_key,
                content_chunks(),
                content_type="application/octet-stream",
            )
        else:
            await storage.write_raw_to_s3(s3_key, content)
        artifact = Artifact(
            id=artifact_id,
            organization_id=organization_id,
            created_by_user_id=created_by_user_id,
            workspace_id=workspace_id,
            logical_path=resolved_path,
            s3_key=s3_key,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
        )
        try:
            self.db.add(artifact)
            await self.db.flush()
        except Exception:
            await storage.delete_raw_from_s3(s3_key)
            raise
        return artifact

    @staticmethod
    def _can_access_artifact(
        caller: Caller,
        artifact: Artifact,
        *,
        permission: str,
    ) -> bool:
        """Require original-user reach and owner-or-named-all authority."""
        if caller.ctx is None or not user_reach(caller).covers(artifact.organization_id):
            return False
        if artifact.created_by_user_id == caller.ctx.user_id:
            return True
        try:
            require_named_permission(caller, permission, artifact.organization_id)
        except PermissionDenied:
            return False
        return True

    async def store_for_caller(
        self,
        caller: Caller,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        storage_family: str = "generated",
        workspace_id: UUID | None = None,
        logical_path: str | None = None,
    ) -> Artifact:
        """Store an artifact with original-user authority and actor attribution.

        A new workspace is scoped to the verified effects principal's current
        execution organization, which must be inside the original caller's
        reach.  Existing workspaces are accepted only when every persisted
        row shares one organization and the original caller owns every row or
        holds scoped ``artifacts.readwrite.all`` for its actual organization.
        """
        caller, organization_id = await self._write_target_for_caller(
            caller, workspace_id=workspace_id
        )
        return await self._store_persist(
            filename=filename,
            content_type=content_type,
            content=content,
            created_by_user_id=caller.principal.user_id,
            organization_id=organization_id,
            storage_family=storage_family,
            workspace_id=workspace_id,
            logical_path=logical_path,
        )

    async def authorize_workspace_write_for_caller(
        self, caller: Caller, *, workspace_id: UUID | None
    ) -> tuple[Caller, UUID | None]:
        """Refresh and authorize an SDK write that will persist in a job later.

        The resolved organization is part of the authorization result.  A
        durable job must retain it rather than substituting the effects
        actor's current organization when writing into an existing workspace.
        """
        return await self._write_target_for_caller(caller, workspace_id=workspace_id)

    async def _write_target_for_caller(
        self, caller: Caller, *, workspace_id: UUID | None
    ) -> tuple[Caller, UUID | None]:
        caller = await refresh_artifact_caller(self.db, caller)
        organization_id = caller.principal.organization_id
        if workspace_id is not None:
            rows = list(
                (
                    await self.db.execute(
                        select(Artifact).where(Artifact.workspace_id == workspace_id)
                    )
                ).scalars().all()
            )
            if rows:
                organizations = {row.organization_id for row in rows}
                if len(organizations) != 1 or any(
                    not self._can_access_artifact(
                        caller, row, permission="artifacts.readwrite.all"
                    )
                    for row in rows
                ):
                    raise ArtifactAccessError("Artifact workspace is not accessible.")
                organization_id = rows[0].organization_id
            elif not user_reach(caller).covers(organization_id):
                raise ArtifactAccessError("Artifact workspace is not accessible.")
        elif not user_reach(caller).covers(organization_id):
            raise ArtifactAccessError("Artifact workspace is not accessible.")
        return caller, organization_id

    async def store_trusted_job(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        created_by_user_id: UUID,
        organization_id: UUID | None,
        workspace_id: UUID | None = None,
        logical_path: str | None = None,
    ) -> Artifact:
        """Persist output after a platform job's enqueue authorization.

        This is intentionally separate from caller-authorized SDK storage.  It
        is for durable job handlers that have already validated their target
        and must never become an optional caller/bypass route.
        """
        return await self._store_persist(
            filename=filename,
            content_type=content_type,
            content=content,
            created_by_user_id=created_by_user_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            logical_path=logical_path,
        )

    async def store_conversation_attachment(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
        created_by_user_id: UUID,
        organization_id: UUID | None,
        storage_family: str = "generated",
        workspace_id: UUID,
        logical_path: str,
    ) -> Artifact:
        """Persist a Chat attachment after its conversation boundary checks."""
        return await self._store_persist(
            filename=filename,
            content_type=content_type,
            content=content,
            created_by_user_id=created_by_user_id,
            organization_id=organization_id,
            storage_family=storage_family,
            workspace_id=workspace_id,
            logical_path=logical_path,
        )

    async def list_workspace_for_caller(
        self,
        workspace_id: UUID,
        *,
        caller: Caller,
    ) -> list[Artifact]:
        """List the latest accessible version of every logical workspace path."""
        caller = await refresh_artifact_caller(self.db, caller)
        reach_filter = user_reach(caller).where(Artifact.organization_id)
        statement = (
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
        if reach_filter is not None:
            statement = statement.where(reach_filter)
        artifacts = list((await self.db.execute(statement)).scalars().all())
        latest: dict[str, Artifact] = {}
        for artifact in artifacts:
            if not self._can_access_artifact(
                caller, artifact, permission="artifacts.read.all"
            ):
                continue
            path = artifact.logical_path or artifact.filename
            latest.setdefault(path, artifact)
        return list(latest.values())

    async def resolve_workspace_path_for_caller(
        self,
        workspace_id: UUID,
        path: str,
        *,
        caller: Caller,
    ) -> Artifact:
        """Resolve the latest accessible artifact at one logical workspace path."""
        caller = await refresh_artifact_caller(self.db, caller)
        normalized = normalize_artifact_path(path)
        reach_filter = user_reach(caller).where(Artifact.organization_id)
        statement = (
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id)
            .where(Artifact.logical_path == normalized)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
        if reach_filter is not None:
            statement = statement.where(reach_filter)
        for artifact in (await self.db.execute(statement)).scalars().all():
            if self._can_access_artifact(
                caller, artifact, permission="artifacts.read.all"
            ):
                return artifact
        raise ArtifactAccessError(f"Artifact workspace path {normalized} was not found.")

    async def get_for_caller(
        self,
        artifact_id: UUID,
        *,
        caller: Caller,
    ) -> Artifact:
        """Resolve one artifact when original-user authority permits it."""
        caller = await refresh_artifact_caller(self.db, caller)
        artifact = await self.db.scalar(select(Artifact).where(Artifact.id == artifact_id))
        if artifact is None or not self._can_access_artifact(
            caller, artifact, permission="artifacts.read.all"
        ):
            raise ArtifactAccessError("Artifact not found.")
        return artifact

    async def read(self, artifact: Artifact) -> bytes:
        return await get_file_storage_service(self.db).read_uploaded_file(
            artifact.s3_key
        )

    async def generate_download_url(self, artifact: Artifact) -> str:
        """Generate a URL that keeps browser-active artifacts inert."""
        storage = get_file_storage_service(self.db)
        if artifact_requires_inert_storage(
            artifact.filename,
            artifact.content_type,
        ):
            return await storage.generate_presigned_download_url(
                artifact.s3_key,
                response_content_type="application/octet-stream",
                response_content_disposition="attachment",
            )
        return await storage.generate_presigned_download_url(
            artifact.s3_key,
        )

    async def delete(self, artifact: Artifact) -> None:
        await get_file_storage_service(self.db).delete_raw_from_s3(artifact.s3_key)
        await self.db.delete(artifact)
        await self.db.flush()
