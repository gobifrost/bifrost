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
    ("list_agents", "bifrost_agent_list"),
    ("get_agent", "bifrost_agent_get"),
    ("create_agent", "bifrost_agent_create"),
    ("update_agent", "bifrost_agent_update"),
    ("delete_agent", "bifrost_agent_delete"),
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

    MIGRATION._replace("get_agent", "bifrost_agent_get")

    assert len(executed) == 1
    statement = executed[0]
    # A ``sa.text(...).bindparams(...)`` clause — not a plain string — proves
    # the values never touched the SQL text itself.
    assert not isinstance(statement, str)
    compiled = statement.compile()
    assert compiled.params == {
        "old": "get_agent",
        "new": "bifrost_agent_get",
    }
    assert "get_agent" not in str(statement)
    assert "array_replace" in str(statement)


async def test_upgrade_rewrites_stored_pre_r1b_tool_ids(db_session, monkeypatch) -> None:
    """Run the real SQL against Postgres on an Agent stored with pre-R1b ids.

    The old names are the tool ids registered before R1b (``list_agents`` …),
    not the catalog's metadata names — a mismatch here matches zero rows and
    silently strips the tools from every existing Agent.
    """
    from uuid import uuid4

    from sqlalchemy import select

    from src.models.orm import Agent

    agent_id = uuid4()
    db_session.add(
        Agent(
            id=agent_id,
            name=f"mcp-rename-{agent_id}",
            system_prompt="x",
            system_tools=["list_agents", "execute_workflow", "delete_agent"],
            created_by="test",
        )
    )
    await db_session.flush()

    executed: list[object] = []
    monkeypatch.setattr(MIGRATION.op, "execute", executed.append)
    MIGRATION.upgrade()
    for statement in executed:
        await db_session.execute(statement)  # type: ignore[arg-type]

    stored = await db_session.scalar(
        select(Agent.system_tools).where(Agent.id == agent_id)
    )
    assert stored == ["bifrost_agent_list", "execute_workflow", "bifrost_agent_delete"]
