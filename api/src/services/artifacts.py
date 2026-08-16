"""Canonical artifact persistence and opaque-reference resolution."""

from __future__ import annotations

from pathlib import PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.artifacts import ArtifactRef
from src.models.orm import Artifact
from src.services.file_storage.service import get_file_storage_service


class ArtifactAccessError(ValueError):
    """Raised when an artifact reference is missing or outside the caller's scope."""


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

    async def store(
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

    async def list_workspace(
        self,
        workspace_id: UUID,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        is_platform_admin: bool = False,
    ) -> list[Artifact]:
        """List the latest version of every logical file in a workspace."""
        conditions = [Artifact.created_by_user_id == user_id]
        if organization_id is not None:
            conditions.append(Artifact.organization_id == organization_id)
        statement = (
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
        if not is_platform_admin:
            statement = statement.where(or_(*conditions))
        artifacts = list((await self.db.execute(statement)).scalars().all())
        latest: dict[str, Artifact] = {}
        for artifact in artifacts:
            path = artifact.logical_path or artifact.filename
            latest.setdefault(path, artifact)
        return list(latest.values())

    async def resolve_workspace_path(
        self,
        workspace_id: UUID,
        path: str,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        is_platform_admin: bool = False,
    ) -> Artifact:
        """Resolve the latest authorized artifact at one logical path."""
        normalized = normalize_artifact_path(path)
        conditions = [Artifact.created_by_user_id == user_id]
        if organization_id is not None:
            conditions.append(Artifact.organization_id == organization_id)
        statement = (
            select(Artifact)
            .where(Artifact.workspace_id == workspace_id)
            .where(Artifact.logical_path == normalized)
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
            .limit(1)
        )
        if not is_platform_admin:
            statement = statement.where(or_(*conditions))
        artifact = (await self.db.execute(statement)).scalar_one_or_none()
        if artifact is None:
            raise ArtifactAccessError(f"Artifact workspace path {normalized} was not found.")
        return artifact

    async def get_authorized(
        self,
        artifact_id: UUID,
        *,
        user_id: UUID,
        organization_id: UUID | None,
        is_platform_admin: bool = False,
    ) -> Artifact:
        conditions = [Artifact.created_by_user_id == user_id]
        if organization_id is not None:
            conditions.append(Artifact.organization_id == organization_id)
        statement = select(Artifact).where(Artifact.id == artifact_id)
        if not is_platform_admin:
            statement = statement.where(or_(*conditions))
        artifact = (await self.db.execute(statement)).scalar_one_or_none()
        if artifact is None:
            raise ArtifactAccessError("Artifact not found.")
        return artifact

    async def read(self, artifact: Artifact) -> bytes:
        return await get_file_storage_service(self.db).read_uploaded_file(
            artifact.s3_key
        )

    async def delete(self, artifact: Artifact) -> None:
        await get_file_storage_service(self.db).delete_raw_from_s3(artifact.s3_key)
        await self.db.delete(artifact)
        await self.db.flush()
