"""Offline contract tests for the Bifrost SDK adapter used by migration workflows."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

from modules.rewst_migration.storage import StorageConflict

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "functions" / "migration.py"


class FakeFiles:
    def __init__(self, paths: list[str] | None = None) -> None:
        self.paths = paths or []
        self.contents: dict[str, str] = {}
        self.write_error: Exception | None = None

    async def list(self, directory: str, *, location: str) -> list[str]:
        self.list_call = (directory, location)
        return self.paths

    async def stat(self, path: str, *, location: str) -> dict[str, object]:
        return {"exists": path in self.contents, "version": "v1"}

    async def read(self, path: str, *, location: str) -> str:
        return self.contents[path]

    async def write(self, *args, **kwargs) -> None:
        if self.write_error:
            raise self.write_error


class FakeTables:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def upsert(self, *args) -> None:
        if self.error:
            raise self.error


def load_adapter(files: FakeFiles, tables: FakeTables, *, admin: bool):
    fake_bifrost = types.ModuleType("bifrost")
    fake_bifrost.files = files
    fake_bifrost.tables = tables
    fake_bifrost.context = types.SimpleNamespace(
        is_platform_admin=admin, solution_id="solution-1"
    )
    fake_bifrost.config = types.SimpleNamespace(get=None)
    fake_bifrost.workflow = lambda **_kwargs: lambda function: function
    previous = sys.modules.get("bifrost")
    sys.modules["bifrost"] = fake_bifrost
    try:
        spec = importlib.util.spec_from_file_location(
            "test_migration_adapter", MODULE_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            del sys.modules["bifrost"]
        else:
            sys.modules["bifrost"] = previous


class SdkStorageTests(unittest.TestCase):
    def test_lists_all_canonical_states_after_many_nonstate_files(self) -> None:
        files = FakeFiles(
            [f"migrations/a/artifacts/{number}.json" for number in range(300)]
            + ["migrations/a/state.json", "migrations/b/state.json"]
        )
        files.contents = {
            "migrations/a/state.json": json.dumps({"id": "a"}),
            "migrations/b/state.json": json.dumps({"id": "b"}),
        }
        module = load_adapter(files, FakeTables(), admin=True)
        states = asyncio.run(module.BifrostMigrationStorage().list_migrations())
        self.assertEqual([state["id"] for state in states], ["a", "b"])
        self.assertEqual(files.list_call, ("migrations", "migration-data"))

    def test_maps_guarded_write_conflicts_and_tolerates_projection_failure(
        self,
    ) -> None:
        files = FakeFiles()
        files.write_error = RuntimeError("HTTP 409 version_conflict")
        module = load_adapter(
            files, FakeTables(error=RuntimeError("table unavailable")), admin=True
        )
        with self.assertRaises(StorageConflict):
            asyncio.run(
                module.BifrostMigrationStorage().write_json(
                    "migrations/a/state.json", {"id": "a"}, expected_version="v1"
                )
            )
        asyncio.run(module.BifrostMigrationStorage().upsert_projection({"id": "a"}))

    def test_wrapper_denies_nonadmin_before_reading_configuration(self) -> None:
        module = load_adapter(FakeFiles(), FakeTables(), admin=False)
        with self.assertRaisesRegex(PermissionError, "platform administrators"):
            asyncio.run(module.migration_get_overview())


if __name__ == "__main__":
    unittest.main()
