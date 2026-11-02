"""Contract tests for the ad-hoc table uniqueness repair migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261101_restore_table_name_unique.py"
)


class _Result:
    def mappings(self) -> _Result:
        return self

    def all(self) -> list[object]:
        return []


class _Bind:
    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self.calls = calls

    def execute(self, statement) -> _Result:  # type: ignore[no-untyped-def]
        self.calls.append(("preflight", str(statement)))
        return _Result()


class _Op:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.bind = _Bind(self.calls)

    def execute(self, statement) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(("lock", str(statement)))

    def get_bind(self) -> _Bind:
        return self.bind

    def create_index(self, name: str, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(("index", name))


def _load_migration_module():
    assert MIGRATION_PATH.exists()
    spec = importlib.util.spec_from_file_location("table_name_uniqueness_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_locks_before_preflight_and_index_creation() -> None:
    module = _load_migration_module()
    recording_op = _Op()
    original_op = module.op
    module.op = recording_op
    try:
        module.upgrade()
    finally:
        module.op = original_op

    assert recording_op.calls[0] == ("lock", "LOCK TABLE tables IN SHARE MODE")
    assert recording_op.calls[1][0] == "preflight"
    assert "FROM tables" in recording_op.calls[1][1]
    assert recording_op.calls[2:] == [
        ("index", "ix_tables_org_name_unique"),
        ("index", "ix_tables_global_name_unique"),
    ]
