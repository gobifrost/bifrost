"""Canonical Builder Agent identity shared by native and external harnesses."""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.solution_builder import SolutionBuilderProject
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder.scaffold import (
    BUILDER_AGENT_MAX_ITERATIONS,
    BUILDER_AGENT_MAX_TOKEN_BUDGET,
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
    project = await db.get(SolutionBuilderProject, solution.id)
    is_global_workspace = project is not None and project.target_kind == "global_repo"
    global_prompt = """You are the Bifrost Global Workspace coding agent.

Work only through the bounded workspace tools. The workspace is an immutable
proposal copied from the live global _repo. Never modify, add, or delete files
under .bifrost; those manifests are read-only evidence and entity mutations
belong in Bifrost APIs, MCP tools, the CLI, or Solution lifecycle. Make focused,
reviewable source changes. Do not claim that a change is live: a platform
administrator must inspect the diff, validate it, and explicitly apply it.
"""
    values: dict[str, Any] = {
        "name": (
            "Global Workspace Builder"
            if is_global_workspace
            else f"{solution.name} Builder"
        ),
        "description": (
            "Administrator global workspace proposal agent"
            if is_global_workspace
            else "Private Solution authoring agent"
        ),
        "system_prompt": (
            global_prompt
            if is_global_workspace
            else (_builder_skill_source() / "SKILL.md").read_text(encoding="utf-8")
        ),
        "bundle_path": None if is_global_workspace else BUILDER_SKILL_BUNDLE_PATH,
        "channels": ["chat"],
        "access_level": AgentAccessLevel.ROLE_BASED,
        "organization_id": solution.organization_id,
        "solution_id": solution.id,
        "owner_user_id": solution.owner_user_id,
        "is_active": True,
        "knowledge_sources": [],
        "system_tools": (
            [
                tool
                for tool in BUILDER_AGENT_SYSTEM_TOOLS
                if tool not in {"validate_solution", "test_solution_build"}
            ]
            if is_global_workspace
            else BUILDER_AGENT_SYSTEM_TOOLS
        ),
        "llm_model": builder_model,
        "max_iterations": BUILDER_AGENT_MAX_ITERATIONS,
        "max_token_budget": BUILDER_AGENT_MAX_TOKEN_BUDGET,
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
