"""Platform-wide AI behavior settings that are independent of model profiles."""

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.config import SystemConfig
from src.models.orm.agent_runs import AgentRun

AI_BEHAVIOR_CATEGORY = "ai"
AI_BEHAVIOR_KEY = "behavior"
AgentExecutionMode = Literal["delegated", "automatic"]
DEFAULT_AGENT_EXECUTION_MODE: AgentExecutionMode = "delegated"
AGENT_EXECUTION_MODE_METADATA_KEY = "agent_execution_mode"
UNSET = object()


class AIBehaviorService:
    """Read and write non-model AI behavior settings."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_default_system_prompt(self) -> str | None:
        row = await self._get_row()
        if not row or not row.value_json:
            return None
        value = row.value_json.get("default_system_prompt")
        return value if isinstance(value, str) and value.strip() else None

    async def get_agent_execution_mode(self) -> AgentExecutionMode:
        row = await self._get_row()
        if not row or not row.value_json:
            return DEFAULT_AGENT_EXECUTION_MODE
        value = row.value_json.get("agent_execution_mode")
        return value if value in {"delegated", "automatic"} else DEFAULT_AGENT_EXECUTION_MODE

    async def resolve_agent_run_execution_mode(self, run: AgentRun) -> AgentExecutionMode:
        """Pin an owning run to its mode before it starts executing.

        The durable run metadata, never request input, is the source for a
        resumed run. This keeps a setting change from changing a
        resumed run's execution topology.
        """
        persisted = (run.run_metadata or {}).get(AGENT_EXECUTION_MODE_METADATA_KEY)
        if persisted in {"delegated", "automatic"}:
            return persisted
        mode = await self.get_agent_execution_mode()
        run.run_metadata = {
            **(run.run_metadata or {}),
            AGENT_EXECUTION_MODE_METADATA_KEY: mode,
        }
        return mode

    async def set_default_system_prompt(
        self,
        prompt: str | None,
        *,
        updated_by: str | None = None,
    ) -> str | None:
        behavior = await self.update_behavior(
            default_system_prompt=prompt,
            updated_by=updated_by,
        )
        return behavior["default_system_prompt"]

    async def update_behavior(
        self,
        *,
        default_system_prompt: str | None | object = UNSET,
        agent_execution_mode: AgentExecutionMode | object = UNSET,
        updated_by: str | None = None,
    ) -> dict[str, str | None]:
        """Update only supplied behavior settings while retaining stored extensions."""
        # SystemConfig has no natural-key uniqueness constraint. Serialize the
        # read/modify/write for this shared JSON document so independent tabs
        # changing prompt and execution mode cannot erase each other.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"{AI_BEHAVIOR_CATEGORY}/{AI_BEHAVIOR_KEY}"},
        )
        row = await self._get_row()
        values = dict(row.value_json or {}) if row else {}
        if default_system_prompt is not UNSET:
            prompt = default_system_prompt
            assert prompt is None or isinstance(prompt, str)
            values["default_system_prompt"] = prompt.strip() if prompt and prompt.strip() else None
        if agent_execution_mode is not UNSET:
            assert agent_execution_mode in {"delegated", "automatic"}
            values["agent_execution_mode"] = agent_execution_mode
        now = datetime.now(timezone.utc)
        if row:
            row.value_json = values
            row.updated_at = now
            row.updated_by = updated_by
        else:
            row = SystemConfig(
                category=AI_BEHAVIOR_CATEGORY,
                key=AI_BEHAVIOR_KEY,
                value_json=values,
                created_by=updated_by,
                updated_by=updated_by,
            )
            self.session.add(row)
        await self.session.flush()
        return {
            "default_system_prompt": self._prompt_from_values(values),
            "agent_execution_mode": self._mode_from_values(values),
        }

    @staticmethod
    def _prompt_from_values(values: dict) -> str | None:
        value = values.get("default_system_prompt")
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _mode_from_values(values: dict) -> AgentExecutionMode:
        value = values.get("agent_execution_mode")
        return value if value in {"delegated", "automatic"} else DEFAULT_AGENT_EXECUTION_MODE

    async def _get_row(self) -> SystemConfig | None:
        return (
            await self.session.execute(
                select(SystemConfig).where(
                    SystemConfig.category == AI_BEHAVIOR_CATEGORY,
                    SystemConfig.key == AI_BEHAVIOR_KEY,
                    SystemConfig.organization_id.is_(None),
                )
            )
        ).scalars().first()
