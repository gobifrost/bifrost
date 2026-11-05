import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, select

from src.models.contracts.ai_behavior import AIBehaviorResponse, AIBehaviorUpdate
from src.models.orm.config import SystemConfig
from src.services.ai_behavior_service import AIBehaviorService


def test_execution_mode_wire_contract_allows_partial_updates():
    response = AIBehaviorResponse.model_json_schema()
    update = AIBehaviorUpdate.model_json_schema()

    assert "agent_execution_mode" in response["required"]
    assert "agent_execution_mode" not in update.get("required", [])
    assert "default" not in update["properties"]["agent_execution_mode"]
    assert update["properties"]["agent_execution_mode"]["type"] == "string"
    assert AIBehaviorUpdate(default_system_prompt="Keep it brief.").model_fields_set == {
        "default_system_prompt"
    }


@pytest.mark.asyncio
async def test_behavior_prompt_round_trip(db_session):
    service = AIBehaviorService(db_session)

    assert await service.get_default_system_prompt() is None
    assert await service.set_default_system_prompt("  Be concise.  ", updated_by="admin@example.com") == "Be concise."

    assert await service.get_default_system_prompt() == "Be concise."
    row = (
        await db_session.execute(
            select(SystemConfig).where(
                SystemConfig.category == "ai",
                SystemConfig.key == "behavior",
            )
        )
    ).scalar_one()
    assert row.updated_by == "admin@example.com"


@pytest.mark.asyncio
async def test_behavior_prompt_can_be_cleared(db_session):
    service = AIBehaviorService(db_session)
    await service.set_default_system_prompt("Use citations.")

    assert await service.set_default_system_prompt("   ") is None
    assert await service.get_default_system_prompt() is None


@pytest.mark.asyncio
async def test_behavior_execution_mode_defaults_to_delegated_and_preserves_other_settings(db_session):
    db_session.add(SystemConfig(
        category="ai",
        key="behavior",
        value_json={"future_setting": "retained"},
    ))
    await db_session.flush()
    service = AIBehaviorService(db_session)

    assert await service.get_agent_execution_mode() == "delegated"
    await service.update_behavior(
        agent_execution_mode="automatic",
        updated_by="admin@example.com",
    )
    await service.update_behavior(default_system_prompt="Use citations.")

    assert await service.get_agent_execution_mode() == "automatic"
    assert await service.get_default_system_prompt() == "Use citations."
    row = (
        await db_session.execute(
            select(SystemConfig).where(
                SystemConfig.category == "ai",
                SystemConfig.key == "behavior",
            )
        )
    ).scalar_one()
    assert row.value_json == {
        "default_system_prompt": "Use citations.",
        "agent_execution_mode": "automatic",
        "future_setting": "retained",
    }


@pytest.mark.asyncio
async def test_run_mode_is_pinned_from_metadata_and_never_from_agent_input(db_session):
    service = AIBehaviorService(db_session)
    service.get_agent_execution_mode = AsyncMock(return_value="delegated")
    resumed_run = MagicMock()
    resumed_run.run_metadata = {"agent_execution_mode": "automatic"}
    resumed_run.input = {"agent_execution_mode": "delegated"}

    assert await service.resolve_agent_run_execution_mode(resumed_run) == "automatic"
    service.get_agent_execution_mode.assert_not_awaited()

    fresh_run = MagicMock()
    fresh_run.run_metadata = {}
    fresh_run.input = {"agent_execution_mode": "automatic"}

    assert await service.resolve_agent_run_execution_mode(fresh_run) == "delegated"
    assert fresh_run.run_metadata == {"agent_execution_mode": "delegated"}


@pytest.mark.asyncio
async def test_concurrent_behavior_updates_preserve_prompt_and_mode(async_session_factory):
    async with async_session_factory() as db:
        original = [
            {
                "value_json": row.value_json,
                "created_by": row.created_by,
                "updated_by": row.updated_by,
            }
            for row in (
                await db.execute(
                    select(SystemConfig).where(
                        SystemConfig.category == "ai",
                        SystemConfig.key == "behavior",
                    )
                )
            ).scalars()
        ]
        await db.execute(delete(SystemConfig).where(
            SystemConfig.category == "ai",
            SystemConfig.key == "behavior",
        ))
        await db.commit()

    async def update_prompt() -> None:
        async with async_session_factory() as db:
            await AIBehaviorService(db).update_behavior(
                default_system_prompt="Use citations."
            )
            await db.commit()

    async def update_mode() -> None:
        async with async_session_factory() as db:
            await AIBehaviorService(db).update_behavior(
                agent_execution_mode="automatic"
            )
            await db.commit()

    try:
        await asyncio.gather(update_prompt(), update_mode())

        async with async_session_factory() as db:
            behavior = AIBehaviorService(db)
            assert await behavior.get_default_system_prompt() == "Use citations."
            assert await behavior.get_agent_execution_mode() == "automatic"
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(SystemConfig).where(
                SystemConfig.category == "ai",
                SystemConfig.key == "behavior",
            ))
            db.add_all([
                SystemConfig(
                    category="ai",
                    key="behavior",
                    value_json=row["value_json"],
                    created_by=row["created_by"],
                    updated_by=row["updated_by"],
                )
                for row in original
            ])
            await db.commit()
