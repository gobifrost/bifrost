import importlib.util
from pathlib import Path

import pytest


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/20260419_apps_repo_path_not_null_unique.py"
)


class _Rows:
    def fetchall(self):
        return [("apps/real\n\x1b[31mFORGED", 2)]


class _Bind:
    def execute(self, _statement):
        return _Rows()


class _Op:
    def execute(self, _statement):
        return None

    def get_bind(self):
        return _Bind()

    def alter_column(self, *_args, **_kwargs):
        raise AssertionError("migration should stop before altering the column")

    def create_index(self, *_args, **_kwargs):
        raise AssertionError("migration should stop before creating the index")


def test_duplicate_repo_paths_escape_log_control_characters() -> None:
    spec = importlib.util.spec_from_file_location("apps_repo_path_migration", MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = _Op()

    with pytest.raises(RuntimeError) as exc_info:
        migration.upgrade()

    message = str(exc_info.value)
    assert "\n" not in message
    assert "\x1b" not in message
    assert r"\n\x1b[31mFORGED" in message
