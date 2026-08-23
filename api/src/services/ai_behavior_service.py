"""Platform-wide AI behavior settings that are independent of model profiles."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.config import SystemConfig

AI_BEHAVIOR_CATEGORY = "ai"
AI_BEHAVIOR_KEY = "behavior"


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

    async def set_default_system_prompt(
        self,
        prompt: str | None,
        *,
        updated_by: str | None = None,
    ) -> str | None:
        normalized = prompt.strip() if prompt and prompt.strip() else None
        row = await self._get_row()
        now = datetime.now(timezone.utc)
        if row:
            row.value_json = {"default_system_prompt": normalized}
            row.updated_at = now
            row.updated_by = updated_by
        else:
            row = SystemConfig(
                category=AI_BEHAVIOR_CATEGORY,
                key=AI_BEHAVIOR_KEY,
                value_json={"default_system_prompt": normalized},
                created_by=updated_by,
                updated_by=updated_by,
            )
            self.session.add(row)
        await self.session.flush()
        return normalized

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
