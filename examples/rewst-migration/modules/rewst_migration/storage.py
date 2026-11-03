"""Revisioned private migration state storage.

The production adapter maps this small protocol to a declared Bifrost managed
file location.  The canonical JSON document is authoritative; table rows are
only a discoverable projection and are never used to reconstruct a migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class StorageConflict(ValueError):
    """Raised when a guarded managed-file write observes stale state."""


@dataclass(frozen=True)
class StoredJson:
    value: dict[str, Any]
    version: str | None


class MigrationStorage(Protocol):
    async def list_migrations(self) -> list[dict[str, Any]]: ...

    async def read_json(self, path: str) -> StoredJson | None: ...

    async def write_json(
        self,
        path: str,
        value: dict[str, Any],
        *,
        expected_version: str | None = None,
        create_only: bool = False,
    ) -> None: ...

    async def upsert_projection(self, migration: dict[str, Any]) -> None: ...


class InMemoryMigrationStorage:
    """CAS-capable storage used only by offline unit tests."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[dict[str, Any], int]] = {}
        self.projections: dict[str, dict[str, Any]] = {}

    async def read_json(self, path: str) -> StoredJson | None:
        entry = self._entries.get(path)
        if entry is None:
            return None
        value, revision = entry
        return StoredJson(_copy(value), str(revision))

    async def list_migrations(self) -> list[dict[str, Any]]:
        return [
            _copy(value)
            for path, (value, _revision) in self._entries.items()
            if path.startswith("migrations/") and path.endswith("/state.json")
        ]

    async def write_json(
        self,
        path: str,
        value: dict[str, Any],
        *,
        expected_version: str | None = None,
        create_only: bool = False,
    ) -> None:
        current = self._entries.get(path)
        if create_only and current is not None:
            raise StorageConflict("migration state already exists")
        if expected_version is not None and (
            current is None or str(current[1]) != expected_version
        ):
            raise StorageConflict("migration state changed; refresh and retry")
        revision = 1 if current is None else current[1] + 1
        self._entries[path] = (_copy(value), revision)

    async def upsert_projection(self, migration: dict[str, Any]) -> None:
        self.projections[str(migration["id"])] = _copy(migration)


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    """Copy JSON-compatible state without retaining caller-owned references."""
    import json

    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
