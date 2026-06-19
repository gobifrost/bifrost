"""
Agent indexer for parsing and indexing .agent.yaml files.

Handles agent metadata extraction, tool/delegation synchronization, and ID alignment.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import yaml
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow
from src.models.orm import Agent, AgentTool, AgentDelegation

logger = logging.getLogger(__name__)


class AgentIndexer:
    """
    Indexes .agent.yaml files and synchronizes with the database.

    Handles ID alignment, tool/delegation association, and agent definition updates.
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize the agent indexer.

        Args:
            db: Database session for querying and updating agent records
        """
        self.db = db

    async def index_agent(
        self,
        path: str,
        content: bytes,
    ) -> bool:
        """
        Parse and index agent from .agent.yaml file.

        If the YAML contains an 'id' field, uses that ID (for API-created agents).
        Otherwise generates a new ID (for files synced from git/editor).

        Updates agent definition (name, description, system_prompt, tools, etc.)
        but preserves environment-specific fields (organization_id, access_level).

        Uses ON CONFLICT to update existing agents.

        Args:
            path: File path
            content: File content bytes

        Returns:
            True if content was modified (ID alignment), False otherwise
        """
        content_modified = False

        try:
            agent_data = yaml.safe_load(content.decode("utf-8"))
        except yaml.YAMLError:
            logger.warning(f"Invalid YAML in agent file: {path}")
            return False

        name = agent_data.get("name")
        if not name:
            raise ValueError(f"agent name is required (file: {path})")

        system_prompt = agent_data.get("system_prompt")
        if not system_prompt:
            raise ValueError(f"system_prompt is required (file: {path})")

        # Use ID from JSON if present (for API-created agents), otherwise generate new
        agent_id_str = agent_data.get("id")
        if agent_id_str:
            try:
                agent_id = UUID(agent_id_str)
            except ValueError:
                logger.warning(f"Invalid agent ID in {path}: {agent_id_str}")
                agent_id = uuid4()
                agent_data["id"] = str(agent_id)
                content_modified = True
        else:
            agent_id = uuid4()
            agent_data["id"] = str(agent_id)
            content_modified = True
            logger.info(f"Injecting ID {agent_id} into agent file: {path}")

        # Agents are now "fully virtual" - their path is computed from their ID
        # (e.g., agents/{uuid}.agent.yaml), so we don't need a separate file_path column.
        # We just use the ID from the YAML content directly.

        # Parse channels — validate against AgentChannel enum
        from src.models.enums import AgentChannel

        valid_channel_values = {c.value for c in AgentChannel}
        raw_channels = agent_data.get("channels", ["chat"])
        if not isinstance(raw_channels, list):
            raw_channels = ["chat"]
        channels = [c for c in raw_channels if c in valid_channel_values]
        if not channels:
            channels = ["chat"]

        # Get knowledge_sources (JSONB field)
        knowledge_sources = agent_data.get("knowledge_sources", [])
        if not isinstance(knowledge_sources, list):
            knowledge_sources = []

        now = datetime.now(timezone.utc)

        # Upsert agent - updates definition but NOT organization_id or access_level
        # These env-specific fields are only set via the API, not from file sync
        stmt = insert(Agent).values(
            id=agent_id,
            name=name,
            description=agent_data.get("description"),
            system_prompt=system_prompt,
            channels=channels,
            knowledge_sources=knowledge_sources,
            is_active=agent_data.get("is_active", True),
            llm_model=agent_data.get("llm_model"),
            llm_max_tokens=agent_data.get("llm_max_tokens"),
            system_tools=agent_data.get("system_tools", []),
            created_by="file_sync",
        ).on_conflict_do_update(
            index_elements=[Agent.id],
            set_={
                # Update definition fields from file
                "name": name,
                "description": agent_data.get("description"),
                "system_prompt": system_prompt,
                "channels": channels,
                "knowledge_sources": knowledge_sources,
                "is_active": agent_data.get("is_active", True),
                "llm_model": agent_data.get("llm_model"),
                "llm_max_tokens": agent_data.get("llm_max_tokens"),
                "system_tools": agent_data.get("system_tools", []),
                "updated_at": now,
                # NOTE: organization_id and access_level are NOT updated
                # These are preserved from the database (env-specific)
            },
        )
        await self.db.execute(stmt)

        # Sync tool associations (tool_ids are workflow UUIDs)
        # Accept 'tools' as friendlier alias for 'tool_ids'
        tool_ids = agent_data.get("tool_ids") or agent_data.get("tools", [])
        if isinstance(tool_ids, list):
            # Delete existing tool associations
            await self.db.execute(
                delete(AgentTool).where(AgentTool.agent_id == agent_id)
            )
            # Create new tool associations (with existence check to prevent FK violations)
            # Track added workflow_ids to avoid duplicates (same workflow ref appears multiple times)
            added_tool_ids: set[UUID] = set()
            for tool_id_str in tool_ids:
                try:
                    workflow_id = UUID(tool_id_str)
                    # Skip if already added (dedup)
                    if workflow_id in added_tool_ids:
                        continue
                    # Check if workflow exists before creating FK relationship
                    workflow_exists = await self.db.execute(
                        select(Workflow.id).where(Workflow.id == workflow_id)
                    )
                    if workflow_exists.scalar_one_or_none():
                        self.db.add(AgentTool(agent_id=agent_id, workflow_id=workflow_id))
                        added_tool_ids.add(workflow_id)
                    else:
                        logger.warning(f"Agent {name} references non-existent workflow {workflow_id}")
                except ValueError:
                    logger.warning(f"Invalid tool_id in agent {name}: {tool_id_str}")

        # Sync delegated agent associations
        delegated_agent_ids = agent_data.get("delegated_agent_ids", [])
        if isinstance(delegated_agent_ids, list):
            # Delete existing delegations
            await self.db.execute(
                delete(AgentDelegation).where(AgentDelegation.parent_agent_id == agent_id)
            )
            # Create new delegations (with existence check to prevent FK violations)
            for child_id_str in delegated_agent_ids:
                try:
                    child_agent_id = UUID(child_id_str)
                    # Check if child agent exists before creating FK relationship
                    agent_exists = await self.db.execute(
                        select(Agent.id).where(Agent.id == child_agent_id)
                    )
                    if agent_exists.scalar_one_or_none():
                        self.db.add(AgentDelegation(parent_agent_id=agent_id, child_agent_id=child_agent_id))
                    else:
                        logger.warning(f"Agent {name} references non-existent agent {child_agent_id}")
                except ValueError:
                    logger.warning(f"Invalid delegated_agent_id in agent {name}: {child_id_str}")

        logger.debug(f"Indexed agent: {name} from {path}")
        return content_modified

    async def delete_agent_for_file(self, path: str) -> int:
        """
        Delete the agent associated with a file.

        Called when a file is deleted to clean up agent records from the database.
        For virtual agents, the ID is extracted from the path (agents/{uuid}.agent.yaml).

        Args:
            path: File path that was deleted (e.g., "agents/{uuid}.agent.yaml")

        Returns:
            Number of agents deleted
        """
        # Extract agent ID from path: {uuid}.agent.yaml -> uuid (any directory)
        import re
        match = re.search(r"([a-f0-9-]+)\.agent\.yaml$", path, re.IGNORECASE)
        if not match:
            logger.warning(f"Cannot extract agent ID from path: {path}")
            return 0

        try:
            agent_id = UUID(match.group(1))
        except ValueError:
            logger.warning(f"Invalid UUID in agent path: {path}")
            return 0

        # Delete the agent by ID (cascade will delete agent_tools and agent_delegations)
        stmt = delete(Agent).where(Agent.id == agent_id)
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(f"Deleted agent {agent_id} from database for deleted file: {path}")

        return count
