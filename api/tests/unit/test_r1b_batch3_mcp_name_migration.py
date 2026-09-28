"""Unit tests for the batch-3 MCP tool-name migration (RBAC R1b).

Loads the migration module directly (it isn't importable as a normal package
— alembic version files aren't on the package path) and exercises
``upgrade``/``downgrade`` against a monkeypatched ``_replace`` / ``op.execute``,
plus real Postgres for the ``agents.system_tools`` rewrite and the
``system_configs`` allow/block-list rewrite — matching the pattern used for
``test_r1b_batch2_mcp_name_migration.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260927_r1b_mcp_names_b3.py"
)
SPEC = importlib.util.spec_from_file_location(
    "r1b_batch3_mcp_name_migration", MIGRATION_PATH
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_upgrade_renames_all_persisted_batch3_tools(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )
    monkeypatch.setattr(MIGRATION, "_rewrite_server_config", lambda renames: None)

    MIGRATION.upgrade()

    assert calls == MIGRATION.RENAMES


def test_downgrade_reverses_batch3_tool_names(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MIGRATION, "_replace", lambda old, new: calls.append((old, new))
    )
    monkeypatch.setattr(MIGRATION, "_rewrite_server_config", lambda renames: None)

    MIGRATION.downgrade()

    assert calls == [(new, old) for old, new in reversed(MIGRATION.RENAMES)]


def test_downgrade_rewrites_server_config_with_reversed_names(monkeypatch) -> None:
    captured: list[list[tuple[str, str]]] = []
    monkeypatch.setattr(MIGRATION, "_replace", lambda old, new: None)
    monkeypatch.setattr(
        MIGRATION, "_rewrite_server_config", lambda renames: captured.append(renames)
    )

    MIGRATION.downgrade()

    assert captured == [[(new, old) for old, new in MIGRATION.RENAMES]]


def test_upgrade_rewrites_server_config_with_batch3_renames(monkeypatch) -> None:
    captured: list[list[tuple[str, str]]] = []
    monkeypatch.setattr(MIGRATION, "_replace", lambda old, new: None)
    monkeypatch.setattr(
        MIGRATION, "_rewrite_server_config", lambda renames: captured.append(renames)
    )

    MIGRATION.upgrade()

    assert captured == [MIGRATION.RENAMES]


def test_replace_uses_bound_parameters_not_string_interpolation(monkeypatch) -> None:
    """The executed statement must carry ``old``/``new`` as bind params."""
    executed: list[object] = []
    monkeypatch.setattr(MIGRATION.op, "execute", executed.append)

    MIGRATION._replace("get_organization", "bifrost_organization_get")

    assert len(executed) == 1
    statement = executed[0]
    assert not isinstance(statement, str)
    compiled = statement.compile()
    assert compiled.params == {
        "old": "get_organization",
        "new": "bifrost_organization_get",
    }
    assert "get_organization" not in str(statement)
    assert "array_replace" in str(statement)


async def test_upgrade_rewrites_stored_pre_r1b_batch3_tool_ids(
    db_session, monkeypatch
) -> None:
    """Run the real SQL against Postgres on an Agent stored with pre-batch-3 ids."""
    from uuid import uuid4

    from sqlalchemy import select

    from src.models.orm import Agent

    agent_id = uuid4()
    db_session.add(
        Agent(
            id=agent_id,
            name=f"mcp-rename-batch3-{agent_id}",
            system_prompt="x",
            system_tools=[
                "list_organizations",
                "get_config",
                "create_policy_rule",
                "update_integration",
                "execute_workflow",
            ],
            created_by="test",
        )
    )
    await db_session.flush()

    executed: list[object] = []
    monkeypatch.setattr(MIGRATION.op, "execute", executed.append)
    monkeypatch.setattr(MIGRATION, "_rewrite_server_config", lambda renames: None)
    MIGRATION.upgrade()
    for statement in executed:
        await db_session.execute(statement)  # type: ignore[arg-type]

    stored = await db_session.scalar(
        select(Agent.system_tools).where(Agent.id == agent_id)
    )
    assert stored == [
        "bifrost_organization_list",
        "bifrost_config_get",
        "bifrost_policy_rule_create",
        "bifrost_integration_update",
        "execute_workflow",
    ]


async def test_upgrade_rewrites_server_config_allow_and_block_lists(
    db_session, monkeypatch
) -> None:
    """Exercise the real ``system_configs`` rewrite against Postgres.

    Covers: a mix of old ids in both lists, a null allowlist elsewhere
    untouched by this row, an unrelated key untouched, and dedupe when both
    old and new spellings are already present.
    """
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO system_configs "
            "(id, category, key, value_json, organization_id, created_by, updated_by, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'mcp', 'server_config', CAST(:value AS jsonb), "
            "NULL, 'test', 'test', NOW(), NOW())"
        ),
        {
            "value": json.dumps(
                {
                    "enabled": True,
                    "allowed_tool_ids": [
                        "list_organizations",
                        "get_integration",
                        "bifrost_integration_get",  # already-renamed dup
                        "execute_workflow",
                    ],
                    "blocked_tool_ids": ["delete_config", "create_integration"],
                }
            )
        },
    )
    await db_session.flush()

    monkeypatch.setattr(MIGRATION, "_replace", lambda old, new: None)
    executed: list[object] = []
    monkeypatch.setattr(MIGRATION.op, "execute", executed.append)
    MIGRATION.upgrade()
    for statement in executed:
        await db_session.execute(statement)  # type: ignore[arg-type]

    row = (
        await db_session.execute(
            text(
                "SELECT value_json FROM system_configs "
                "WHERE category = 'mcp' AND key = 'server_config' "
                "AND organization_id IS NULL"
            )
        )
    ).mappings().first()
    assert row is not None
    value = row["value_json"]
    assert set(value["allowed_tool_ids"]) == {
        "bifrost_organization_list",
        "bifrost_integration_get",
        "execute_workflow",
    }
    assert len(value["allowed_tool_ids"]) == 3  # deduped
    assert set(value["blocked_tool_ids"]) == {
        "bifrost_config_delete",
        "bifrost_integration_create",
    }
    assert value["enabled"] is True


async def test_upgrade_preserves_null_allowlist(db_session, monkeypatch) -> None:
    """``allowed_tool_ids: null`` (all tools) must not become ``[]``."""
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO system_configs "
            "(id, category, key, value_json, organization_id, created_by, updated_by, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'mcp', 'server_config', CAST(:value AS jsonb), "
            "NULL, 'test', 'test', NOW(), NOW())"
        ),
        {
            "value": json.dumps(
                {
                    "enabled": True,
                    "allowed_tool_ids": None,
                    "blocked_tool_ids": ["list_policy_rules"],
                }
            )
        },
    )
    await db_session.flush()

    monkeypatch.setattr(MIGRATION, "_replace", lambda old, new: None)
    executed: list[object] = []
    monkeypatch.setattr(MIGRATION.op, "execute", executed.append)
    MIGRATION.upgrade()
    for statement in executed:
        await db_session.execute(statement)  # type: ignore[arg-type]

    row = (
        await db_session.execute(
            text(
                "SELECT value_json FROM system_configs "
                "WHERE category = 'mcp' AND key = 'server_config' "
                "AND organization_id IS NULL"
            )
        )
    ).mappings().first()
    assert row is not None
    value = row["value_json"]
    assert value["allowed_tool_ids"] is None
    assert value["blocked_tool_ids"] == ["bifrost_policy_rule_list"]


async def test_upgrade_leaves_unrelated_config_key_untouched(
    db_session, monkeypatch
) -> None:
    """A config row with a different category/key must not be touched."""
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO system_configs "
            "(id, category, key, value_json, organization_id, created_by, updated_by, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'other', 'unrelated', CAST(:value AS jsonb), "
            "NULL, 'test', 'test', NOW(), NOW())"
        ),
        {"value": json.dumps({"allowed_tool_ids": ["list_organizations"]})},
    )
    await db_session.flush()

    monkeypatch.setattr(MIGRATION, "_replace", lambda old, new: None)
    executed: list[object] = []
    monkeypatch.setattr(MIGRATION.op, "execute", executed.append)
    MIGRATION.upgrade()
    for statement in executed:
        await db_session.execute(statement)  # type: ignore[arg-type]

    row = (
        await db_session.execute(
            text(
                "SELECT value_json FROM system_configs "
                "WHERE category = 'other' AND key = 'unrelated'"
            )
        )
    ).mappings().first()
    assert row is not None
    assert row["value_json"]["allowed_tool_ids"] == ["list_organizations"]
