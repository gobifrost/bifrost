"""Shared source-backed standalone App build, upload, activation, and cleanup."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import update

from shared.app_build_errors import (
    AppBuildMemoryError,
    app_build_memory_message,
    subprocess_memory_error,
)
from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobFailure
from src.models.orm.applications import Application
from src.services.application_sdk_status import current_sdk_metadata
from src.services.application_source_artifact import ApplicationSourceArtifactStorage
from src.services.application_source_resolver import ResolvedApplicationSource
from src.services.solutions.app_build import SolutionAppBuilder

logger = logging.getLogger(__name__)


async def rebuild_application_from_source(
    *,
    application: Application | None = None,
    application_id: UUID | None = None,
    deployment_id: UUID,
    source: ResolvedApplicationSource,
    retained_source_zip: Path | None = None,
    expected_active_deployment_id: UUID | None = None,
    expected_sdk_package_version: str | None = None,
    expected_sdk_fingerprint: str | None = None,
    expected_sdk_contract_version: int | None = None,
    expected_sdk_built_at: datetime | None = None,
    enforce_expected_state: bool = False,
) -> dict[str, str]:
    if application is None and application_id is None:
        raise ValueError("application or application_id is required")
    app_id = application.id if application is not None else application_id
    if app_id is None:
        raise ValueError("application id is required")

    builder = SolutionAppBuilder()
    source_artifacts = ApplicationSourceArtifactStorage()
    activated = False
    old_deployment_id: UUID | None = None

    try:
        try:
            sdk_metadata = await asyncio.to_thread(current_sdk_metadata)
            if not sdk_metadata.fingerprint or sdk_metadata.contract_version is None:
                raise PlatformJobFailure(
                    "sdk_provenance_unavailable",
                    "Current SDK provenance is unavailable.",
                )
            dist = await asyncio.to_thread(
                builder.compile_dist,
                app_id,
                source.files,
                source.dependencies,
            )
        except PlatformJobFailure:
            raise
        except AppBuildMemoryError as exc:
            raise PlatformJobFailure("memory_pressure", str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            memory_error = subprocess_memory_error(
                exc.returncode, exc.stderr, exc.stdout
            )
            if memory_error is not None:
                raise PlatformJobFailure("memory_pressure", str(memory_error)) from exc
            detail = (exc.stderr or exc.stdout or b"").decode(errors="replace")[-4000:]
            raise PlatformJobFailure(
                "app_build_failed",
                f"Vite build failed.\n{detail}" if detail else "Vite build failed.",
            ) from exc
        except MemoryError as exc:
            raise PlatformJobFailure(
                "memory_pressure", app_build_memory_message()
            ) from exc
        except Exception as exc:
            raise PlatformJobFailure("app_build_failed", str(exc)) from exc

        await builder.upload_deployment(app_id, deployment_id, dist)

        source_zip_to_store: Path | None = None
        with tempfile.TemporaryDirectory(prefix="bifrost-app-retained-source-") as tmp:
            if source.source_kind == "independent":
                source_zip_to_store = retained_source_zip or Path(tmp) / "source.zip"
                if retained_source_zip is None:
                    await asyncio.to_thread(_write_source_zip, source_zip_to_store, source.files)
                await source_artifacts.write_deployment_source(
                    app_id, deployment_id, source_zip_to_store
                )

        async with get_db_context() as db:
            app = await _get_activation_app(db, app_id, application)
            if app is None or app.app_model != "standalone_v2":
                raise PlatformJobFailure(
                    "app_not_deployable",
                    "The App no longer supports standalone V2 builds.",
                )
            if source.source_kind == "independent" and app.solution_id is not None:
                raise PlatformJobFailure(
                    "app_not_deployable",
                    "The App no longer supports independent deployment.",
                )
            if enforce_expected_state:
                _assert_expected_state(
                    app,
                    expected_active_deployment_id=expected_active_deployment_id,
                    expected_sdk_package_version=expected_sdk_package_version,
                    expected_sdk_fingerprint=expected_sdk_fingerprint,
                    expected_sdk_contract_version=expected_sdk_contract_version,
                    expected_sdk_built_at=expected_sdk_built_at,
                )
            old_deployment_id = app.active_deployment_id
            deployed_at = datetime.now(timezone.utc)
            provenance_values = {
                "active_deployment_id": deployment_id,
                "deployed_at": deployed_at,
                "sdk_package_version": sdk_metadata.package_version,
                "sdk_fingerprint": sdk_metadata.fingerprint,
                "sdk_contract_version": sdk_metadata.contract_version,
                "sdk_built_at": deployed_at,
            }
            if app.solution_id is None:
                for field, value in provenance_values.items():
                    setattr(app, field, value)
                await db.flush()
            else:
                await _activate_solution_app(
                    db,
                    app,
                    provenance_values=provenance_values,
                    old_deployment_id=old_deployment_id,
                    enforce_expected_state=enforce_expected_state,
                    expected_sdk_package_version=expected_sdk_package_version,
                    expected_sdk_fingerprint=expected_sdk_fingerprint,
                    expected_sdk_contract_version=expected_sdk_contract_version,
                    expected_sdk_built_at=expected_sdk_built_at,
                )
        activated = True

        if old_deployment_id and old_deployment_id != deployment_id:
            try:
                await builder.delete_deployment(app_id, old_deployment_id)
            except Exception:
                logger.warning(
                    "Failed to remove superseded App deployment %s",
                    old_deployment_id,
                    exc_info=True,
                )
            if source.source_kind == "independent":
                try:
                    await source_artifacts.delete_deployment_source(
                        app_id, old_deployment_id
                    )
                except Exception:
                    logger.warning(
                        "Failed to remove superseded App source %s",
                        old_deployment_id,
                        exc_info=True,
                    )

        return {
            "application_id": str(app_id),
            "deployment_id": str(deployment_id),
        }
    finally:
        if not activated:
            try:
                await source_artifacts.delete_deployment_source(
                    app_id, deployment_id
                )
            except Exception:
                logger.warning(
                    "Failed to clean incomplete retained App source %s",
                    deployment_id,
                    exc_info=True,
                )
            try:
                await builder.delete_deployment(app_id, deployment_id)
            except Exception:
                logger.warning(
                    "Failed to clean incomplete App deployment %s",
                    deployment_id,
                    exc_info=True,
                )


async def _get_activation_app(
    db, app_id: UUID, application: Application | None
) -> Application | None:
    get = getattr(db, "get", None)
    if get is None:
        return application
    return await get(Application, app_id)


async def _activate_solution_app(
    db,
    app: Application,
    *,
    provenance_values: dict[str, object],
    old_deployment_id: UUID | None,
    enforce_expected_state: bool,
    expected_sdk_package_version: str | None,
    expected_sdk_fingerprint: str | None,
    expected_sdk_contract_version: int | None,
    expected_sdk_built_at: datetime | None,
) -> None:
    stmt = (
        update(Application)
        .where(
            Application.id == app.id,
            Application.solution_id == app.solution_id,
            Application.active_deployment_id == old_deployment_id,
        )
        .values(**provenance_values)
        .execution_options(synchronize_session=False)
    )
    if enforce_expected_state:
        stmt = stmt.where(
            Application.sdk_package_version == expected_sdk_package_version,
            Application.sdk_fingerprint == expected_sdk_fingerprint,
            Application.sdk_contract_version == expected_sdk_contract_version,
            Application.sdk_built_at == expected_sdk_built_at,
        )
    result = await db.execute(stmt)
    if result.rowcount != 1:
        raise PlatformJobFailure(
            "app_sdk_update_stale" if enforce_expected_state else "app_not_deployable",
            (
                "The App deployment changed before the SDK update could activate."
                if enforce_expected_state
                else "The App no longer supports standalone V2 builds."
            ),
        )


def _assert_expected_state(
    app: Application,
    *,
    expected_active_deployment_id: UUID | None,
    expected_sdk_package_version: str | None,
    expected_sdk_fingerprint: str | None,
    expected_sdk_contract_version: int | None,
    expected_sdk_built_at: datetime | None,
) -> None:
    if app.active_deployment_id != expected_active_deployment_id:
        raise PlatformJobFailure(
            "app_sdk_update_stale",
            "The App deployment changed before the SDK update could activate.",
        )
    expected = {
        "sdk_package_version": expected_sdk_package_version,
        "sdk_fingerprint": expected_sdk_fingerprint,
        "sdk_contract_version": expected_sdk_contract_version,
        "sdk_built_at": expected_sdk_built_at,
    }
    for field, value in expected.items():
        if getattr(app, field) != value:
            raise PlatformJobFailure(
                "app_sdk_update_stale",
                "The App SDK provenance changed before the SDK update could activate.",
            )


def _write_source_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
