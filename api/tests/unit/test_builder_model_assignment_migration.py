"""Forward-migration coverage for the Builder model profile assignment."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260827_builder_model_assignment.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "_builder_model_assignment_migration",
        MIGRATION_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_allows_and_seeds_builder_assignment(monkeypatch):
    migration = _load_migration()
    calls: list[tuple[str, tuple, dict]] = []

    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *args, **kwargs: calls.append(("drop", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda *args, **kwargs: calls.append(("check", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda sql: calls.append(("execute", (str(sql),), {})),
    )

    migration.upgrade()

    check_sql = next(args[2] for kind, args, _kwargs in calls if kind == "check")
    seed_sql = next(args[0] for kind, args, _kwargs in calls if kind == "execute")
    assert "'builder'" in check_sql
    assert "SELECT 'builder', profile_id" in seed_sql
    assert "WHERE assignment_key = 'primary'" in seed_sql
