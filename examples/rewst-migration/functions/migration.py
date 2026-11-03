"""Admin-only workflow adapters for the read-only Rewst Migration pilot."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from bifrost import config, context, files, tables, workflow

from modules.rewst_migration.core import MigrationService
from modules.rewst_migration.rewst import RewstReadClient
from modules.rewst_migration.storage import MigrationStorage, StoredJson


LOCATION = "migration-data"
TABLE = "rewst_migration_projects"
LOGGER = logging.getLogger(__name__)


class BifrostMigrationStorage(MigrationStorage):
    async def list_migrations(self) -> list[dict[str, Any]]:
        paths = await files.list("migrations", location=LOCATION)
        states: list[dict[str, Any]] = []
        for path in paths:
            if path.startswith("migrations/") and path.endswith("/state.json"):
                stored = await self.read_json(path)
                if stored is not None:
                    states.append(stored.value)
        return states

    async def read_json(self, path: str) -> StoredJson | None:
        stat = await files.stat(path, location=LOCATION)
        if not stat.get("exists"):
            return None
        raw = await files.read(path, location=LOCATION)
        return StoredJson(json.loads(raw), stat.get("version"))

    async def write_json(
        self,
        path: str,
        value: dict[str, Any],
        *,
        expected_version: str | None = None,
        create_only: bool = False,
    ) -> None:
        try:
            await files.write(
                path,
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
                location=LOCATION,
                expected_version=expected_version,
                create_only=create_only,
            )
        except Exception as error:
            if (
                "409" in str(error)
                or "version_conflict" in str(error)
                or "file_exists" in str(error)
            ):
                from modules.rewst_migration.storage import StorageConflict

                raise StorageConflict(
                    "migration state changed; refresh and retry"
                ) from error
            raise

    async def upsert_projection(self, migration: dict[str, Any]) -> None:
        try:
            await tables.upsert(TABLE, migration["id"], migration)
        except Exception:
            # The private state file is already the canonical committed record.
            # A projection miss is repairable from that record on the next read.
            LOGGER.warning("Rewst migration index projection could not be updated")


async def _service() -> MigrationService:
    _require_admin()
    api_url = await config.get(
        "rewst_migration_api_url", default="https://api.rewst.io/graphql"
    )
    root_org_id = await config.get("rewst_migration_org_id", default="")
    session = await config.get("rewst_migration_app_session", default=None)
    return MigrationService(
        storage=BifrostMigrationStorage(),
        root_org_id=str(root_org_id or ""),
        api_url=str(api_url),
        graph_factory=lambda: RewstReadClient(
            endpoint=str(api_url), session=_required_session(session)
        ),
        now=lambda: datetime.now(timezone.utc).isoformat(),
        solution_id=getattr(context, "solution_id", None),
    )


def _require_admin() -> None:
    if not getattr(context, "is_platform_admin", False):
        raise PermissionError(
            "Rewst Migration pilot is restricted to platform administrators"
        )


def _required_session(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Configure the Rewst app session before using live mode")
    return value


@workflow(category="Migration")
async def migration_get_overview() -> dict[str, Any]:
    """Return this install's private migration index and safe connection metadata."""
    service = await _service()
    cookie = await config.get("rewst_migration_app_session", default=None)
    return await service.overview(cookie_configured=bool(cookie))


@workflow(category="Migration")
async def migration_create(
    name: str, mode: str = "sample", source_org_ids: list[str] | None = None
) -> dict[str, Any]:
    """Create a read-only sample or authorized live migration record."""
    return await (await _service()).create(
        name=name, mode=mode, source_org_ids=source_org_ids
    )


@workflow(category="Migration")
async def migration_validate_connection() -> dict[str, Any]:
    """Run a bounded read-only source connection check."""
    return await (await _service()).validate_connection()


@workflow(category="Migration")
async def migration_export_environment(migration_id: str) -> dict[str, Any]:
    """Capture bounded, read-only source evidence; no source mutation occurs."""
    return await (await _service()).export_environment(migration_id)


@workflow(category="Migration")
async def migration_get_context(migration_id: str) -> dict[str, Any]:
    """Return a migration's plan, coverage, items, artifacts, and handoff."""
    return await (await _service()).get_context(migration_id)


@workflow(category="Migration")
async def migration_save_plan(
    migration_id: str, markdown: str, expected_revision: int
) -> dict[str, Any]:
    """Save an immutable plan revision using optimistic concurrency."""
    return await (await _service()).save_plan(migration_id, markdown, expected_revision)


@workflow(category="Migration")
async def migration_update_item(
    migration_id: str,
    item_id: str,
    status: str,
    evidence: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Advance a migration item; tested status requires evidence."""
    return await (await _service()).update_item(
        migration_id, item_id, status, evidence, expected_revision
    )


@workflow(category="Migration")
async def migration_read_artifact(
    migration_id: str, artifact_id: str
) -> dict[str, Any]:
    """Read one private migration artifact without exposing configuration secrets."""
    return await (await _service()).read_artifact(migration_id, artifact_id)
