"""Unit tests for the Agent MCP tool-name migration (RBAC R1b).

Loads the migration module directly (it isn't importable as a normal package
— alembic version files aren't on the package path) and exercises
``upgrade``/``downgrade`` against a monkeypatched ``_replace`` /
``op.execute``, matching the pattern used for other data migrations.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260927_agent_mcp_names.py"
)
SPEC = importlib.util.spec_from_file_location(
    "agent_mcp_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

EXPECTED_RENAMES = [
    ("bifrost_list_agents", "bifrost_agent_list"),
    ("bifrost_get_agent", "bifrost_agent_get"),
    ("bifrost_create_agent", "bifrost_agent_create"),
    ("bifrost_update_agent", "bifrost_agent_update"),
    ("bifrost_delete_agent", "bifrost_agent_delete"),
]


def test_upgrade_renames_all_persisted_agent_crud_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.upgrade()

    assert calls == EXPECTED_RENAMES


def test_downgrade_reverses_agent_crud_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in EXPECTED_RENAMES]


def test_replace_uses_bound_parameters_not_string_interpolation(monkeypatch) -> None:
    """The executed statement must carry ``old``/``new`` as bind params.

    Guards against a future edit reintroducing f-string SQL (the CodeQL
    concern this migration was written to avoid).
    """
    executed: list[object] = []
    monkeypatch.setattr(MIGRATION.op, "execute", executed.append)

    MIGRATION._replace("bifrost_get_agent", "bifrost_agent_get")

    assert len(executed) == 1
    statement = executed[0]
    # A ``sa.text(...).bindparams(...)`` clause — not a plain string — proves
    # the values never touched the SQL text itself.
    assert not isinstance(statement, str)
    compiled = statement.compile()
    assert compiled.params == {
        "old": "bifrost_get_agent",
        "new": "bifrost_agent_get",
    }
    assert "bifrost_get_agent" not in str(statement)
    assert "array_replace" in str(statement)
