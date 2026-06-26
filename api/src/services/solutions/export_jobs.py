"""Helpers for durable async Solution backup export artifacts."""

from __future__ import annotations

import tempfile
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decrypt_secret, encrypt_secret
from src.models.enums import ConfigType
from src.models.contracts.solutions import SolutionExportOptions
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution
from src.services.file_storage import FileStorageService
from src.services.solutions.capture import SolutionCaptureService
from src.services.solutions.export import (
    add_live_content_to_workspace_zip_file,
    build_workspace_zip_for_export,
)
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage

SOLUTION_EXPORT_ARTIFACT_CONTENT_TYPE = "application/zip"
SOLUTION_EXPORT_ARTIFACT_PREFIX = "solution-exports"

logger = logging.getLogger(__name__)


def _unlink_best_effort(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to remove temporary export file %s", path, exc_info=True)


def encrypt_export_options(options: SolutionExportOptions) -> str:
    """Encrypt export options for DB storage."""
    return encrypt_secret(options.model_dump_json())


def decrypt_export_options(encrypted_options: str) -> SolutionExportOptions:
    """Decrypt export options read from DB storage."""
    return SolutionExportOptions.model_validate_json(decrypt_secret(encrypted_options))


def export_artifact_storage_key(solution_id: UUID | str, job_id: UUID | str) -> str:
    """Return the stable raw S3 key for a completed export artifact."""
    return f"{SOLUTION_EXPORT_ARTIFACT_PREFIX}/{solution_id}/{job_id}.zip"


def export_artifact_filename(solution: Solution) -> str:
    """Return the download filename for a Solution export artifact."""
    return f"{solution.slug}-{solution.version or 'unversioned'}.zip"


def export_options_to_capture_flags(options: SolutionExportOptions) -> dict[str, bool]:
    """Map durable backup options onto the existing synchronous export flags."""
    return {
        "include_imports": True,
        "include_values": options.include_configs or options.include_secrets,
        "include_data": options.include_tables,
        "include_files": options.include_files,
    }


def filter_config_values_by_options(
    config_values: dict[str, str],
    *,
    secret_keys: set[str],
    options: SolutionExportOptions,
) -> dict[str, str]:
    """Keep only config values selected by the separate config/secret toggles."""
    if options.include_configs and options.include_secrets:
        return config_values
    return {
        key: value
        for key, value in config_values.items()
        if (key in secret_keys and options.include_secrets)
        or (key not in secret_keys and options.include_configs)
    }


async def solution_secret_config_keys(db: AsyncSession, solution_id: UUID) -> set[str]:
    """Return declared secret config keys for a Solution."""
    rows = (
        await db.execute(
            select(SolutionConfigSchema.key, SolutionConfigSchema.type).where(
                SolutionConfigSchema.solution_id == solution_id
            )
        )
    ).all()
    return {
        key
        for key, type_ in rows
        if type_ and str(type_).lower() == ConfigType.SECRET.value
    }


async def apply_config_value_selection(
    db: AsyncSession,
    solution: Solution,
    options: SolutionExportOptions,
    config_values: dict[str, str],
) -> dict[str, str]:
    """Filter captured config values after capture decrypts secret values."""
    if not config_values:
        return config_values
    secret_keys = await solution_secret_config_keys(db, solution.id)
    return filter_config_values_by_options(
        config_values,
        secret_keys=secret_keys,
        options=options,
    )


def export_options_select_runtime_payload(options: SolutionExportOptions) -> bool:
    """Whether the selected export content requires encrypted runtime payloads."""
    return (
        options.include_configs
        or options.include_secrets
        or options.include_tables
        or options.include_files
    )


def validate_export_options_password(options: SolutionExportOptions) -> None:
    """Require a password when selected backup content needs encryption."""
    if export_options_select_runtime_payload(options) and not options.password:
        raise ValueError("backup export requires a password")


async def build_solution_backup_zip_to_path(
    db: AsyncSession,
    solution: Solution,
    options: SolutionExportOptions,
    dest: Path,
) -> None:
    """Build a durable backup export zip for ``solution`` at ``dest``.

    Reuses the synchronous export format: a stored source artifact is copied and
    overlaid with live encrypted runtime content when present; otherwise the
    live bundle is captured and written through the standard export builder.
    """
    validate_export_options_password(options)

    source_tmp = tempfile.NamedTemporaryFile(
        prefix=f"bifrost-solution-source-{solution.id}-",
        suffix=".zip",
        delete=False,
    )
    source_path = Path(source_tmp.name)
    source_tmp.close()
    try:
        artifact = SolutionSourceArtifactStorage(solution.id)
        has_stored_source = await artifact.copy_to_path(source_path)
        bundle = await SolutionCaptureService(db).bundle_for(
            solution,
            **export_options_to_capture_flags(options),
        )
        bundle.config_values = await apply_config_value_selection(
            db,
            solution,
            options,
            bundle.config_values,
        )
        if has_stored_source:
            await add_live_content_to_workspace_zip_file(
                source_path,
                bundle,
                db,
                dest,
                password=options.password or "",
            )
        else:
            await build_workspace_zip_for_export(
                bundle,
                db,
                dest,
                password=options.password,
            )
    except Exception:
        _unlink_best_effort(dest)
        raise
    finally:
        _unlink_best_effort(source_path)


async def build_solution_backup_zip_tempfile(
    db: AsyncSession,
    solution: Solution,
    options: SolutionExportOptions,
) -> Path:
    """Build a durable backup export zip in a caller-owned temporary file."""
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"bifrost-solution-export-{solution.id}-",
        suffix=".zip",
        delete=False,
    )
    out_path = Path(tmp.name)
    tmp.close()
    await build_solution_backup_zip_to_path(db, solution, options, out_path)
    return out_path


async def _path_chunks(path: Path, chunk_size: int = 8 * 1024 * 1024) -> AsyncIterator[bytes]:
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


async def upload_solution_export_artifact(
    db: AsyncSession,
    storage_key: str,
    artifact_path: Path,
) -> tuple[str, int]:
    """Upload a completed export zip to raw S3 and return ``(sha256, size)``."""
    storage = FileStorageService(db)
    return await storage.write_raw_chunks_to_s3(
        storage_key,
        _path_chunks(artifact_path),
        content_type=SOLUTION_EXPORT_ARTIFACT_CONTENT_TYPE,
    )


async def delete_solution_export_artifact(db: AsyncSession, storage_key: str | None) -> None:
    """Delete a stored export artifact by raw S3 key."""
    if not storage_key:
        return
    storage = FileStorageService(db)
    await storage.delete_raw_from_s3(storage_key)


class SolutionExportArtifactService:
    """Small facade used by future schedulers/routes to build and store exports."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_zip_to_path(
        self,
        solution: Solution,
        options: SolutionExportOptions,
        dest: Path,
    ) -> None:
        await build_solution_backup_zip_to_path(self.db, solution, options, dest)

    async def build_zip_tempfile(
        self,
        solution: Solution,
        options: SolutionExportOptions,
    ) -> Path:
        return await build_solution_backup_zip_tempfile(self.db, solution, options)

    async def upload_artifact(
        self,
        storage_key: str,
        artifact_path: Path,
    ) -> tuple[str, int]:
        return await upload_solution_export_artifact(self.db, storage_key, artifact_path)

    async def delete_artifact(self, storage_key: str | None) -> None:
        await delete_solution_export_artifact(self.db, storage_key)

    @staticmethod
    def encrypt_options(options: SolutionExportOptions) -> str:
        return encrypt_export_options(options)

    @staticmethod
    def decrypt_options(encrypted_options: str) -> SolutionExportOptions:
        return decrypt_export_options(encrypted_options)

    @staticmethod
    def artifact_storage_key(solution_id: UUID | str, job_id: UUID | str) -> str:
        return export_artifact_storage_key(solution_id, job_id)

    @staticmethod
    def artifact_filename(solution: Solution) -> str:
        return export_artifact_filename(solution)

    @staticmethod
    def capture_flags(options: SolutionExportOptions) -> dict[str, bool]:
        return export_options_to_capture_flags(options)

    @staticmethod
    def validate_password(options: SolutionExportOptions) -> None:
        validate_export_options_password(options)


__all__ = [
    "SOLUTION_EXPORT_ARTIFACT_CONTENT_TYPE",
    "SOLUTION_EXPORT_ARTIFACT_PREFIX",
    "SolutionExportArtifactService",
    "build_solution_backup_zip_tempfile",
    "build_solution_backup_zip_to_path",
    "decrypt_export_options",
    "delete_solution_export_artifact",
    "encrypt_export_options",
    "export_artifact_filename",
    "export_artifact_storage_key",
    "export_options_select_runtime_payload",
    "export_options_to_capture_flags",
    "filter_config_values_by_options",
    "solution_secret_config_keys",
    "upload_solution_export_artifact",
    "validate_export_options_password",
]
