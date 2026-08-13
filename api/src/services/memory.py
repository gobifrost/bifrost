"""Business logic for ownership-scoped Bifrost memory."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import MemoryEntry, MemoryStore, SystemConfig, User
from src.services.embeddings import get_embedding_client

MEMORY_CONFIG_CATEGORY = "memory"
MEMORY_CONFIG_KEY = "settings"

class MemoryDisabledError(RuntimeError):
    """Raised when platform memory is off or the user has opted out."""


class MemoryConfigurationError(RuntimeError):
    """Raised when memory cannot embed content with the platform AI config."""


class MemoryService:
    """Own settings and entries for one authenticated user."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        organization_id: UUID | None,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.organization_id = organization_id

    async def platform_enabled(self) -> bool:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == MEMORY_CONFIG_CATEGORY,
                SystemConfig.key == MEMORY_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        return bool(config and config.value_json and config.value_json.get("enabled"))

    async def set_platform_enabled(self, enabled: bool, *, updated_by: str) -> None:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == MEMORY_CONFIG_CATEGORY,
                SystemConfig.key == MEMORY_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        now = datetime.now(timezone.utc)
        if config:
            config.value_json = {"enabled": enabled}
            config.updated_at = now
            config.updated_by = updated_by
        else:
            self.session.add(
                SystemConfig(
                    id=uuid4(),
                    category=MEMORY_CONFIG_CATEGORY,
                    key=MEMORY_CONFIG_KEY,
                    value_json={"enabled": enabled},
                    organization_id=None,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
        await self.session.flush()

    async def user_enabled(self) -> bool:
        result = await self.session.execute(
            select(User.memory_enabled).where(User.id == self.user_id)
        )
        enabled = result.scalar_one_or_none()
        return bool(enabled)

    async def set_user_enabled(self, enabled: bool) -> None:
        if enabled and not await self.platform_enabled():
            raise MemoryDisabledError("Memory is not enabled by the platform administrator")
        result = await self.session.execute(select(User).where(User.id == self.user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise LookupError("User not found")
        user.memory_enabled = enabled
        await self.session.flush()

    async def settings(self) -> dict[str, bool]:
        platform_enabled = await self.platform_enabled()
        user_enabled = await self.user_enabled()
        return {
            "platform_enabled": platform_enabled,
            "user_enabled": user_enabled,
            "effective_enabled": platform_enabled and user_enabled,
        }

    async def list_entries(self) -> list[MemoryEntry]:
        store = await self._get_store()
        if store is None:
            return []
        result = await self.session.execute(
            select(MemoryEntry)
            .where(MemoryEntry.store_id == store.id)
            .order_by(MemoryEntry.created_at.desc())
        )
        return list(result.scalars())

    async def save(self, content: str, metadata: dict[str, Any]) -> MemoryEntry:
        await self._require_enabled()
        embedding = await self._embed(content)
        store = await self._get_or_create_store()
        entry = MemoryEntry(
            store_id=store.id,
            content=content,
            doc_metadata=metadata,
            embedding=embedding,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def search(self, query: str, limit: int) -> list[tuple[MemoryEntry, float]]:
        await self._require_enabled()
        store = await self._get_store()
        if store is None:
            return []
        query_embedding = await self._embed(query)
        distance = MemoryEntry.embedding.cosine_distance(query_embedding).label("distance")
        result = await self.session.execute(
            select(MemoryEntry, distance)
            .where(MemoryEntry.store_id == store.id)
            .order_by(distance)
            .limit(limit)
        )
        return [
            (entry, max(0.0, min(1.0, 1.0 - float(raw_distance))))
            for entry, raw_distance in result.all()
        ]

    async def remove(self, memory_id: UUID) -> bool:
        store = await self._get_store()
        if store is None:
            return False
        result = await self.session.execute(
            delete(MemoryEntry)
            .where(
                MemoryEntry.id == memory_id,
                MemoryEntry.store_id == store.id,
            )
            .returning(MemoryEntry.id)
        )
        return result.scalar_one_or_none() is not None

    async def _require_enabled(self) -> None:
        settings = await self.settings()
        if not settings["effective_enabled"]:
            raise MemoryDisabledError(
                "Memory must be enabled by the platform and not disabled by the user"
            )

    async def _embed(self, text: str) -> list[float]:
        try:
            client = await get_embedding_client(self.session)
            return await client.embed_single(text)
        except ValueError as exc:
            raise MemoryConfigurationError(str(exc)) from exc

    def _store_scope(self) -> tuple:
        organization_clause = (
            MemoryStore.organization_id == self.organization_id
            if self.organization_id is not None
            else MemoryStore.organization_id.is_(None)
        )
        return organization_clause, MemoryStore.user_id == self.user_id

    async def _get_store(self) -> MemoryStore | None:
        result = await self.session.execute(
            select(MemoryStore).where(*self._store_scope())
        )
        return result.scalar_one_or_none()

    async def _get_or_create_store(self) -> MemoryStore:
        store = await self._get_store()
        if store is not None:
            return store
        store = MemoryStore(
            organization_id=self.organization_id,
            user_id=self.user_id,
        )
        self.session.add(store)
        await self.session.flush()
        return store
