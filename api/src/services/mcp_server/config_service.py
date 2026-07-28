"""
MCP Configuration Service

Manages MCP server configuration stored in the system_configs table.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.config import SystemConfig

logger = logging.getLogger(__name__)

# Configuration constants
MCP_CONFIG_CATEGORY = "mcp"
MCP_CONFIG_KEY = "server_config"


@dataclass
class MCPConfig:
    """
    MCP server configuration.

    Controls external access to the MCP endpoint. Agent discovery and dispatch
    enforce caller-specific access; this config exposes the master on/off switch
    plus a platform-wide allowlist/blocklist for underlying tools.
    """

    enabled: bool = True
    allowed_tool_ids: list[str] | None = None  # None = all tools
    blocked_tool_ids: list[str] | None = None
    configured_at: datetime | None = None
    configured_by: str | None = None

    @property
    def is_configured(self) -> bool:
        """Whether configuration has been explicitly set."""
        return self.configured_at is not None


class MCPConfigService:
    """
    Service for managing MCP configuration.

    Stores configuration in the system_configs table using the
    established pattern for platform-level settings.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_config(self) -> MCPConfig:
        """
        Get the current MCP configuration.

        Returns:
            MCPConfig with current settings, or defaults if not configured
        """
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == MCP_CONFIG_CATEGORY,
                SystemConfig.key == MCP_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),  # Platform-wide config
            )
        )
        config = result.scalars().first()

        if config is None or config.value_json is None:
            # Return defaults
            return MCPConfig()

        data = config.value_json
        return MCPConfig(
            enabled=data.get("enabled", True),
            allowed_tool_ids=data.get("allowed_tool_ids"),
            blocked_tool_ids=data.get("blocked_tool_ids", []),
            configured_at=config.updated_at,
            configured_by=config.updated_by,
        )

    async def save_config(
        self,
        *,
        enabled: bool = True,
        allowed_tool_ids: list[str] | None = None,
        blocked_tool_ids: list[str] | None = None,
        updated_by: str,
    ) -> MCPConfig:
        """
        Save MCP configuration.

        Args:
            enabled: Whether external MCP access is enabled
            allowed_tool_ids: List of allowed tool IDs (None = all)
            blocked_tool_ids: List of blocked tool IDs
            updated_by: Email of user making the change

        Returns:
            Updated MCPConfig
        """
        config_data = {
            "enabled": enabled,
            "allowed_tool_ids": allowed_tool_ids,
            "blocked_tool_ids": blocked_tool_ids or [],
        }

        # Check if config exists
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == MCP_CONFIG_CATEGORY,
                SystemConfig.key == MCP_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        existing = result.scalars().first()

        now = datetime.now(timezone.utc)

        if existing:
            # Update existing
            existing.value_json = config_data
            existing.updated_by = updated_by
            existing.updated_at = now
            logger.info(f"MCP config updated by {updated_by}")
        else:
            # Create new
            new_config = SystemConfig(
                category=MCP_CONFIG_CATEGORY,
                key=MCP_CONFIG_KEY,
                value_json=config_data,
                created_by=updated_by,
                updated_by=updated_by,
            )
            self.session.add(new_config)
            logger.info(f"MCP config created by {updated_by}")

        return MCPConfig(
            enabled=enabled,
            allowed_tool_ids=allowed_tool_ids,
            blocked_tool_ids=blocked_tool_ids or [],
            configured_at=now,
            configured_by=updated_by,
        )

    async def delete_config(self) -> bool:
        """
        Delete MCP configuration (revert to defaults).

        Uses a bulk DELETE so any duplicate rows left by an upstream polluter
        (e.g. a test that PUT enabled=True without cleaning up) are wiped in
        a single round-trip rather than just removing the first match.

        Returns:
            True if at least one config row was deleted, False otherwise
        """
        result = await self.session.execute(
            sa_delete(SystemConfig).where(
                SystemConfig.category == MCP_CONFIG_CATEGORY,
                SystemConfig.key == MCP_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        deleted = result.rowcount or 0

        if deleted:
            logger.info(
                "MCP config deleted (reverted to defaults)",
                extra={"rows_deleted": deleted},
            )
            return True

        return False


# Cached config for use in auth middleware
_cached_config: MCPConfig | None = None
_cache_time: datetime | None = None
_CACHE_TTL_SECONDS = 60  # Cache config for 1 minute


async def get_mcp_config_cached(session: AsyncSession) -> MCPConfig:
    """
    Get MCP config with caching.

    Used by the auth middleware to avoid hitting the database on every request.
    Cache is refreshed every 60 seconds.

    Args:
        session: Database session

    Returns:
        Cached or fresh MCPConfig
    """
    global _cached_config, _cache_time

    now = datetime.now(timezone.utc)

    # Check if cache is valid
    if _cached_config is not None and _cache_time is not None:
        age = (now - _cache_time).total_seconds()
        if age < _CACHE_TTL_SECONDS:
            return _cached_config

    # Refresh cache
    service = MCPConfigService(session)
    _cached_config = await service.get_config()
    _cache_time = now

    return _cached_config


def invalidate_mcp_config_cache() -> None:
    """Invalidate the MCP config cache (call after updates)."""
    global _cached_config, _cache_time
    _cached_config = None
    _cache_time = None
