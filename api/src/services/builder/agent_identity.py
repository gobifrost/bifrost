"""Canonical Builder Agent identity shared by native and external harnesses."""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder.scaffold import (
    BUILDER_AGENT_SYSTEM_TOOLS,
    BUILDER_SKILL_BUNDLE_PATH,
    _builder_skill_source,
    builder_agent_id,
)
from src.services.llm_config_service import LLMConfigService


async def ensure_builder_agent(
    db: AsyncSession,
    *,
    solution: Solution,
) -> Agent:
    """Create or refresh the deterministic Agent for one private Solution.

    The Agent exists as soon as the first Builder session exists, even when the
    hoster has not configured native AI. That lets an authorized external MCP
    harness drive the exact same workspace and Skill without first spending a
    native model turn.
    """
    owner = (
        await db.get(User, solution.owner_user_id)
        if solution.owner_user_id is not None
        else None
    )
    config = await LLMConfigService(db).get_config()
    builder_model = (
        config.builder_model.strip()
        if config is not None
        and isinstance(config.builder_model, str)
        and config.builder_model.strip()
        else config.model.strip()
        if config is not None
        and isinstance(config.model, str)
        and config.model.strip()
        else None
    )
    agent_id = builder_agent_id(solution.id)
    values: dict[str, Any] = {
        "name": f"{solution.name} Builder",
        "description": "Private Solution authoring agent",
        "system_prompt": (_builder_skill_source() / "SKILL.md").read_text(
            encoding="utf-8"
        ),
        "bundle_path": BUILDER_SKILL_BUNDLE_PATH,
        "channels": ["chat"],
        "access_level": AgentAccessLevel.ROLE_BASED,
        "organization_id": solution.organization_id,
        "solution_id": solution.id,
        "owner_user_id": solution.owner_user_id,
        "is_active": True,
        "knowledge_sources": [],
        "system_tools": BUILDER_AGENT_SYSTEM_TOOLS,
        "llm_model": builder_model,
        "created_by": owner.email if owner else "system@gobifrost.local",
    }
    agent = await db.get(Agent, agent_id)
    if agent is None:
        agent = Agent(id=agent_id, **values)
        db.add(agent)
    else:
        # Builder Agent configuration is derived from the current Solution and
        # canonical Skill. Core UPDATE intentionally bypasses the portable
        # entity write guard just like the pre-existing native-turn path did.
        await db.execute(update(Agent).where(Agent.id == agent_id).values(**values))
    await db.flush()
    return agent
