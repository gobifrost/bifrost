import pytest
from sqlalchemy import select

from src.models.orm.config import SystemConfig
from src.services.ai_behavior_service import AIBehaviorService


@pytest.mark.asyncio
async def test_behavior_prompt_round_trip(db_session):
    service = AIBehaviorService(db_session)

    assert await service.get_default_system_prompt() is None
    assert await service.set_default_system_prompt("  Be concise.  ", updated_by="admin@example.com") == "Be concise."
    await db_session.commit()

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
