"""
Configuration resolver with transparent secret handling and Redis caching.

This module provides unified configuration access that:
- Checks Redis cache first for fast reads
- Falls back to PostgreSQL on cache miss
- Populates cache on miss for subsequent reads
- Automatically decrypts secret values (secrets are stored encrypted in cache)
"""

import json
import logging
from typing import Any, TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models import ConfigType

if TYPE_CHECKING:
    from src.sdk.context import Organization

logger = logging.getLogger(__name__)


class ConfigResolver:
    """
    Resolves configuration values with transparent secret handling.

    Features:
    - Automatic config type detection (secret vs plain values)
    - Transparent decryption for secret type configs
    - Type parsing for int, bool, json types
    """

    def __init__(self):
        """Initialize the configuration resolver."""
        pass

    async def get_config(
        self,
        org_id: str,
        key: str,
        config_data: dict[str, Any],
        default: Any = ...,  # Sentinel value to distinguish None from "not provided"
    ) -> Any:
        """
        Get configuration value with transparent secret decryption.

        Logic:
        1. Check if key exists in config_data
        2. Determine config type (if available)
        3. If type is secret, decrypt the value
        4. Otherwise, return/parse plain value
        5. If key not found, return default or raise KeyError

        Args:
            org_id: Organization identifier
            key: Configuration key
            config_data: Configuration dictionary
                        Expected format: {key: {"value": "...", "type": "secret"}}
                        or {key: "plain_value"}
            default: Default value if key not found. If not provided, raises KeyError.

        Returns:
            Configuration value (with secret decrypted if needed)

        Raises:
            KeyError: If key not found and no default provided
        """
        # Check if key exists
        if key not in config_data:
            if default is ...:  # No default provided
                raise KeyError(f"Configuration key '{key}' not found for org '{org_id}'")
            logger.debug(f"Config key not found: {key}, returning default")
            return default

        config_entry = config_data[key]

        # Handle simple string values (backwards compatibility)
        if isinstance(config_entry, str):
            logger.debug(f"Retrieved plain config value: {key}")
            return config_entry

        # Handle structured config entries
        if isinstance(config_entry, dict):
            config_type = config_entry.get("type")
            config_value = config_entry.get("value")

            if config_value is None:
                raise ValueError(f"Config value is None for key '{key}'")

            # If type is secret, decrypt the value
            if config_type == ConfigType.SECRET.value or config_type == "secret":
                return self._decrypt_secret(key, config_value)

            # Otherwise return parsed plain value
            logger.debug(f"Retrieved plain config value: {key} (type: {config_type})")
            return self._parse_value(config_value, config_type)

        # Fallback: return value as-is
        logger.debug(f"Retrieved config value: {key}")
        return config_entry

    def _decrypt_secret(self, config_key: str, encrypted_value: str) -> str:
        """
        Decrypt a secret value.

        Args:
            config_key: Configuration key (for logging)
            encrypted_value: The encrypted value to decrypt

        Returns:
            Decrypted secret value

        Raises:
            ValueError: If decryption fails
        """
        try:
            from src.core.security import decrypt_secret

            logger.debug(f"Decrypting secret for config '{config_key}'")
            from src.core.secret_string import SecretString
            return SecretString(decrypt_secret(encrypted_value))

        except Exception as e:
            error_msg = f"Failed to decrypt secret for config '{config_key}': {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e

    def _parse_value(self, value: str, config_type: str | None) -> Any:
        """
        Parse configuration value based on type.

        Args:
            value: String value from config
            config_type: Config type (string, int, bool, json, secret)

        Returns:
            Parsed value in appropriate type

        Raises:
            ValueError: If value cannot be parsed for the specified type
        """
        try:
            if config_type == ConfigType.INT.value or config_type == "int":
                return int(value)
            elif config_type == ConfigType.BOOL.value or config_type == "bool":
                return value.lower() in ("true", "1", "yes")
            elif config_type == ConfigType.JSON.value or config_type == "json":
                return json.loads(value)
            else:
                # STRING or unknown type - return as string
                return value
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            raise ValueError(f"Could not parse value '{value}' as type '{config_type}': {e}") from e

    async def get_organization(
        self, org_id: str, db: AsyncSession | None = None
    ) -> "Organization | None":
        """
        Get organization by ID.

        Uses Redis cache first, falls back to PostgreSQL on miss.

        Args:
            org_id: Organization ID (UUID or "ORG:uuid" format)
            db: Optional AsyncSession. If not provided, creates its own.

        Returns:
            Organization object or None if not found
        """
        from uuid import UUID
        from src.sdk.context import Organization

        # Parse org_id - may be "ORG:uuid" or just "uuid"
        if org_id.startswith("ORG:"):
            org_uuid = org_id[4:]
        else:
            org_uuid = org_id

        try:
            UUID(org_uuid)  # Validate format
        except ValueError:
            logger.warning(f"Invalid organization ID format: {org_id}")
            return None

        # Try Redis cache first
        cached = await self._get_org_from_cache(org_uuid)
        if cached is not None:
            logger.debug(f"Org cache hit for org_id={org_uuid}")
            return Organization(
                id=cached["id"],
                name=cached["name"],
                is_active=cached["is_active"],
                is_provider=cached.get("is_provider", False),
            )

        # Cache miss - load from PostgreSQL
        logger.debug(f"Org cache miss for org_id={org_uuid}, loading from DB")

        from sqlalchemy import select
        from src.core.database import get_session_factory
        from src.models import Organization as OrgModel

        org_uuid_obj = UUID(org_uuid)

        async def _fetch(session: AsyncSession) -> "Organization | None":
            result = await session.execute(
                select(OrgModel).where(OrgModel.id == org_uuid_obj)
            )
            org_entity = result.scalar_one_or_none()

            if not org_entity:
                logger.debug(f"Organization not found: {org_id}")
                return None

            # Populate cache for next time
            await self._set_org_cache(
                org_id=str(org_entity.id),
                name=org_entity.name,
                domain=org_entity.domain,
                is_active=org_entity.is_active,
                is_provider=org_entity.is_provider,
            )

            return Organization(
                id=str(org_entity.id),
                name=org_entity.name,
                is_active=org_entity.is_active,
                is_provider=org_entity.is_provider,
            )

        if db is not None:
            return await _fetch(db)
        else:
            session_factory = get_session_factory()
            async with session_factory() as session:
                return await _fetch(session)

    async def _get_org_from_cache(self, org_id: str) -> dict[str, Any] | None:
        """
        Get organization from Redis cache.

        Returns None on cache miss or error.
        """
        try:
            from src.core.cache import get_shared_redis, org_key

            r = await get_shared_redis()
            redis_key = org_key(org_id)

            data = await r.get(redis_key)
            if not data:
                return None

            # Handle bytes from Redis
            data_str = data.decode() if isinstance(data, bytes) else data
            return json.loads(data_str)

        except Exception as e:
            logger.warning(f"Failed to get org from cache: {e}")
            return None

    async def _set_org_cache(
        self,
        org_id: str,
        name: str,
        domain: str | None,
        is_active: bool,
        is_provider: bool = False,
    ) -> None:
        """
        Populate Redis cache with org data.
        """
        try:
            from src.core.cache import get_shared_redis, org_key, TTL_ORGS

            r = await get_shared_redis()
            redis_key = org_key(org_id)

            cache_value = json.dumps({
                "id": org_id,
                "name": name,
                "domain": domain,
                "is_active": is_active,
                "is_provider": is_provider,
            })

            await r.set(redis_key, cache_value, ex=TTL_ORGS)
            logger.debug(f"Populated org cache for org_id={org_id}")

        except Exception as e:
            logger.warning(f"Failed to populate org cache: {e}")

    async def load_config_for_scope(
        self,
        scope: str,
        db: AsyncSession | None = None,
        *,
        include_global_secrets: bool = False,
    ) -> dict[str, Any]:
        """
        Load all config for a scope (org_id or "GLOBAL").

        Uses Redis cache first, falls back to PostgreSQL on miss.
        Secrets are stored encrypted in cache and decrypted at get_config() time.

        Returns config as dict: {key: {"value": v, "type": t}, ...}

        Args:
            scope: "GLOBAL" or organization ID
            db: Optional AsyncSession. If not provided, creates its own.
            include_global_secrets: For org scopes, include global secret
                fallback values. CLI-facing callers leave this false so a
                global secret cannot be decrypted through an org fallback.

        Returns:
            Configuration dictionary
        """
        from uuid import UUID

        # Normalize org_id for cache key
        if scope == "GLOBAL":
            org_id_for_cache = None
        elif scope.startswith("ORG:"):
            org_id_for_cache = scope[4:]
        else:
            org_id_for_cache = scope

        # Try Redis cache first
        cached = await self._get_config_from_cache(org_id_for_cache)
        if cached is not None:
            if org_id_for_cache is not None and not include_global_secrets:
                has_unscoped_secret = any(
                    isinstance(entry, dict)
                    and entry.get("type") == ConfigType.SECRET.value
                    and entry.get("scope") not in {"org", "global"}
                    for entry in cached.values()
                )
                if has_unscoped_secret:
                    cached = None
                else:
                    cached = {
                        key: entry
                        for key, entry in cached.items()
                        if not (
                            isinstance(entry, dict)
                            and entry.get("type") == ConfigType.SECRET.value
                            and entry.get("scope") == "global"
                        )
                    }
        if cached is not None:
            logger.debug(f"Config cache hit for scope={log_safe(scope)}")
            return cached

        # Cache miss - load from PostgreSQL
        logger.debug(f"Config cache miss for scope={log_safe(scope)}, loading from DB")

        from sqlalchemy import select
        from src.core.database import get_session_factory
        from src.models import Config

        async def _fetch(session: AsyncSession) -> dict[str, Any]:
            config_dict: dict[str, Any] = {}

            # For GLOBAL, get configs with no organization_id
            # For org scope, get global + org-specific configs (org overrides global)
            # Always exclude integration-scoped configs (integration_id IS NOT NULL)
            # — those are accessed via the integration system, not config.get()
            if scope == "GLOBAL":
                result = await session.execute(
                    select(Config).where(
                        Config.organization_id.is_(None),
                        Config.integration_id.is_(None),
                    )
                )
                for config in result.scalars():
                    config_dict[config.key] = {
                        "value": config.value.get("value") if isinstance(config.value, dict) else config.value,
                        "type": config.config_type.value if config.config_type else "string",
                        "scope": "global",
                    }
            else:
                try:
                    org_uuid_obj = UUID(org_id_for_cache) if org_id_for_cache else None
                except ValueError:
                    logger.warning(f"Invalid scope format: {log_safe(scope)}")
                    return config_dict

                # Get global configs first
                global_result = await session.execute(
                    select(Config).where(
                        Config.organization_id.is_(None),
                        Config.integration_id.is_(None),
                    )
                )
                for config in global_result.scalars():
                    if (
                        not include_global_secrets
                        and config.config_type == ConfigType.SECRET
                    ):
                        continue
                    config_dict[config.key] = {
                        "value": config.value.get("value") if isinstance(config.value, dict) else config.value,
                        "type": config.config_type.value if config.config_type else "string",
                        "scope": "global",
                    }

                # Get org-specific configs (these override global)
                result = await session.execute(
                    select(Config).where(
                        Config.organization_id == org_uuid_obj,
                        Config.integration_id.is_(None),
                    )
                )
                for config in result.scalars():
                    config_dict[config.key] = {
                        "value": config.value.get("value") if isinstance(config.value, dict) else config.value,
                        "type": config.config_type.value if config.config_type else "string",
                        "scope": "org",
                    }

            # Populate cache for next time
            await self._set_config_cache(org_id_for_cache, config_dict)

            return config_dict

        if db is not None:
            return await _fetch(db)
        else:
            session_factory = get_session_factory()
            async with session_factory() as session:
                return await _fetch(session)

    async def _get_config_from_cache(self, org_id: str | None) -> dict[str, Any] | None:
        """
        Get all config from Redis cache.

        Returns None on cache miss or error.
        """
        try:
            from src.core.cache import get_shared_redis, config_hash_key

            r = await get_shared_redis()
            hash_key = config_hash_key(org_id)

            # Get all fields from the hash
            data = await r.hgetall(hash_key)  # type: ignore[misc]
            if not data:
                return None

            # Parse JSON values
            config_dict: dict[str, Any] = {}
            for key, value in data.items():
                # Handle bytes from Redis
                key_str = key.decode() if isinstance(key, bytes) else key
                value_str = value.decode() if isinstance(value, bytes) else value
                try:
                    config_dict[key_str] = json.loads(value_str)
                except json.JSONDecodeError:
                    # Fallback for plain string values
                    config_dict[key_str] = {"value": value_str, "type": "string"}

            return config_dict

        except Exception as e:
            logger.warning(f"Failed to get config from cache: {e}")
            return None

    async def _set_config_cache(self, org_id: str | None, config_dict: dict[str, Any]) -> None:
        """
        Populate Redis cache with config data.
        """
        if not config_dict:
            return

        try:
            from src.core.cache import get_shared_redis, config_hash_key, TTL_CONFIG

            r = await get_shared_redis()
            hash_key = config_hash_key(org_id)

            # Convert to JSON strings for Redis
            mapping = {
                key: json.dumps(value) for key, value in config_dict.items()
            }

            # Set all fields in the hash
            await r.hset(hash_key, mapping=mapping)  # type: ignore[misc]
            await r.expire(hash_key, TTL_CONFIG)

            logger.debug(f"Populated config cache for org={org_id}, keys={len(config_dict)}")

        except Exception as e:
            logger.warning(f"Failed to populate config cache: {e}")
