"""Resolve editable and built-in instructions for the default MCP gateway."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Organization, SystemConfig

INSTRUCTIONS_CONFIG_CATEGORY = "required_instructions"
INSTRUCTIONS_CONFIG_KEY = "content"

MEMORY_INSTRUCTIONS = (
    "These built-in memory safety requirements take precedence over global and "
    "organization instructions. Memory is enabled for this user. Search memory "
    "when prior preferences, "
    "decisions, or durable context may help with the current task. Save only "
    "durable, reusable information that is explicitly worth remembering; do "
    "not save secrets, temporary task state, or unverified assumptions. Tell "
    "the user whenever you save or remove a memory."
)


class RequiredInstructionsService:
    """Store scoped instructions and compose the instructions for one user."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID | None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id

    async def configured(self, organization_id: UUID | None) -> str:
        organization_clause = (
            SystemConfig.organization_id.is_(None)
            if organization_id is None
            else SystemConfig.organization_id == organization_id
        )
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == INSTRUCTIONS_CONFIG_CATEGORY,
                SystemConfig.key == INSTRUCTIONS_CONFIG_KEY,
                organization_clause,
            )
        )
        config = result.scalars().first()
        if not config or not config.value_json:
            return ""
        value = config.value_json.get("instructions")
        return value if isinstance(value, str) else ""

    async def set_configured(
        self,
        instructions: str,
        *,
        organization_id: UUID | None,
        updated_by: str,
    ) -> str:
        normalized = instructions.strip()
        organization_clause = (
            SystemConfig.organization_id.is_(None)
            if organization_id is None
            else SystemConfig.organization_id == organization_id
        )
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == INSTRUCTIONS_CONFIG_CATEGORY,
                SystemConfig.key == INSTRUCTIONS_CONFIG_KEY,
                organization_clause,
            )
        )
        config = result.scalars().first()

        if not normalized:
            if config:
                await self.session.execute(
                    delete(SystemConfig).where(SystemConfig.id == config.id)
                )
            await self.session.flush()
            return ""

        now = datetime.now(timezone.utc)
        if config:
            config.value_json = {"instructions": normalized}
            config.updated_at = now
            config.updated_by = updated_by
        else:
            self.session.add(
                SystemConfig(
                    id=uuid4(),
                    category=INSTRUCTIONS_CONFIG_CATEGORY,
                    key=INSTRUCTIONS_CONFIG_KEY,
                    value_json={"instructions": normalized},
                    organization_id=organization_id,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
        await self.session.flush()
        return normalized

    async def organization_exists(self, organization_id: UUID) -> bool:
        result = await self.session.execute(
            select(Organization.id).where(Organization.id == organization_id)
        )
        return result.scalar_one_or_none() is not None

    async def resolved(self, *, memory_enabled: bool) -> list[str]:
        """Return non-empty Markdown sections in stable precedence order."""
        sections: list[str] = []
        if memory_enabled:
            sections.append(self._section("Memory", MEMORY_INSTRUCTIONS))

        global_instructions = await self.configured(None)
        if global_instructions:
            sections.append(self._section("Global Instructions", global_instructions))

        if self.organization_id is not None:
            organization_instructions = await self.configured(self.organization_id)
            if organization_instructions:
                sections.append(
                    self._section(
                        "Organization Instructions",
                        organization_instructions,
                    )
                )
        return sections

    @staticmethod
    def _section(title: str, instructions: str) -> str:
        return f"# {title}\n\n{instructions.strip()}"
