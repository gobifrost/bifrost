"""Config repository."""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select

from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType
from src.models import (
    Config as ConfigModel,
    ConfigResponse,
    ConfigType,
    SetConfigRequest,
    UpdateConfigRequest,
)
from src.models.enums import ConfigType as ConfigTypeEnum
from src.models.orm.integrations import Integration
from src.repositories.org_scoped import OrgScopedRepository

logger = logging.getLogger(__name__)


class ConfigRepository(OrgScopedRepository[ConfigModel]):  # type: ignore[type-var]
    """Repository for configuration values."""

    model = ConfigModel
    role_table = None

    async def list_configs(
        self,
        filter_type: OrgFilterType = OrgFilterType.ORG_PLUS_GLOBAL,
    ) -> list[ConfigResponse]:
        """List configs with specified filter type."""
        query = select(self.model, Integration.name.label("integration_name")).outerjoin(
            Integration,
            and_(
                self.model.integration_id == Integration.id,
                Integration.is_deleted.is_(False),
            ),
        )

        if filter_type == OrgFilterType.ALL:
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            query = query.where(self.model.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            query = query.where(self.model.organization_id == self.org_id)
        else:
            if self.org_id is not None:
                query = query.where(
                    or_(
                        self.model.organization_id == self.org_id,
                        self.model.organization_id.is_(None),
                    )
                )
            else:
                query = query.where(self.model.organization_id.is_(None))

        query = query.order_by(self.model.key)

        result = await self.session.execute(query)
        rows = result.all()

        schemas = []
        for row in rows:
            config = row[0]
            integration_name = row[1]

            raw_value = (
                config.value.get("value")
                if isinstance(config.value, dict)
                else config.value
            )
            display_value = (
                "[SECRET]"
                if config.config_type == ConfigTypeEnum.SECRET
                else raw_value
            )

            schemas.append(
                ConfigResponse(
                    id=config.id,
                    key=config.key,
                    value=display_value,
                    type=ConfigType(config.config_type.value)
                    if config.config_type
                    else ConfigType.STRING,
                    scope="org" if config.organization_id else "GLOBAL",
                    org_id=str(config.organization_id)
                    if config.organization_id
                    else None,
                    integration_id=str(config.integration_id)
                    if config.integration_id
                    else None,
                    integration_name=integration_name,
                    description=config.description,
                    updated_at=config.updated_at,
                    updated_by=config.updated_by,
                )
            )
        return schemas

    async def get_config(self, key: str) -> ConfigModel | None:
        """Get config by key with cascade scoping: org-specific > global."""
        return await self.get(key=key)

    async def merged_for_sdk(self) -> dict[str, Any]:
        """Return the full merged config dict for this scope."""
        from src.core.cache.keys import (
            TTL_CONFIG,
            config_hash_key_versioned,
        )
        from src.core.cache.redis_client import get_shared_redis

        org_id_str = str(self.org_id) if self.org_id is not None else None

        try:
            redis = await get_shared_redis()
            hash_key = await config_hash_key_versioned(redis, org_id_str)
            cached = await redis.hgetall(hash_key)  # type: ignore[misc]
            if cached:
                out: dict[str, Any] = {}
                for key, value in cached.items():
                    key_str = key.decode() if isinstance(key, bytes) else key
                    value_str = value.decode() if isinstance(value, bytes) else value
                    try:
                        out[key_str] = json.loads(value_str)
                    except json.JSONDecodeError:
                        out[key_str] = {"value": value_str, "type": "string"}
                return out
        except Exception as e:
            logger.warning(f"Config cache read failed: {e}")

        config_dict: dict[str, Any] = {}

        global_q = select(self.model).where(
            self.model.organization_id.is_(None),
            self.model.integration_id.is_(None),
        )
        global_rows = (await self.session.execute(global_q)).scalars()
        for config in global_rows:
            value = (
                config.value.get("value")
                if isinstance(config.value, dict)
                else config.value
            )
            config_dict[config.key] = {
                "value": value,
                "type": config.config_type.value if config.config_type else "string",
            }

        if self.org_id is not None:
            org_q = select(self.model).where(
                self.model.organization_id == self.org_id,
                self.model.integration_id.is_(None),
            )
            org_rows = (await self.session.execute(org_q)).scalars()
            for config in org_rows:
                value = (
                    config.value.get("value")
                    if isinstance(config.value, dict)
                    else config.value
                )
                config_dict[config.key] = {
                    "value": value,
                    "type": config.config_type.value
                    if config.config_type
                    else "string",
                }

        if config_dict:
            try:
                redis = await get_shared_redis()
                hash_key = await config_hash_key_versioned(redis, org_id_str)
                mapping = {key: json.dumps(value) for key, value in config_dict.items()}
                await redis.hset(hash_key, mapping=mapping)  # type: ignore[misc]
                await redis.expire(hash_key, TTL_CONFIG)
            except Exception as e:
                logger.warning(f"Config cache write failed: {e}")

        logger.debug(
            f"Loaded {len(config_dict)} config entries for org={log_safe(org_id_str)}"
        )
        return config_dict

    async def get_config_strict(self, key: str) -> ConfigModel | None:
        """Get config strictly in current org scope."""
        query = select(self.model).where(
            self.model.key == key,
            self.model.organization_id == self.org_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def set_config(
        self, request: SetConfigRequest, updated_by: str
    ) -> ConfigResponse:
        """Create or update a config in current org scope."""
        now = datetime.now(timezone.utc)

        stored_value = request.value
        if request.type == ConfigType.SECRET:
            from src.core.security import encrypt_secret

            stored_value = encrypt_secret(request.value)

        db_config_type = (
            ConfigTypeEnum(request.type.value)
            if request.type
            else ConfigTypeEnum.STRING
        )

        existing = await self.get_config_strict(request.key)

        if existing:
            existing.value = {"value": stored_value}
            existing.config_type = db_config_type
            existing.description = request.description
            existing.updated_at = now
            existing.updated_by = updated_by
            await self.session.flush()
            await self.session.refresh(existing)
            config = existing
        else:
            config = ConfigModel(
                key=request.key,
                value={"value": stored_value},
                config_type=db_config_type,
                description=request.description,
                organization_id=self.org_id,
                created_at=now,
                updated_at=now,
                updated_by=updated_by,
            )
            self.session.add(config)
            await self.session.flush()
            await self.session.refresh(config)

        logger.info(f"Set config {log_safe(request.key)} in org {self.org_id}")

        value = config.value.get("value") if isinstance(config.value, dict) else config.value
        return ConfigResponse(
            id=config.id,
            key=config.key,
            value=value,
            type=request.type if request.type else ConfigType.STRING,
            scope="org" if config.organization_id else "GLOBAL",
            org_id=str(config.organization_id) if config.organization_id else None,
            description=config.description,
            updated_at=config.updated_at,
            updated_by=config.updated_by,
        )

    async def update_config_by_id(
        self,
        config_id: UUID,
        request: UpdateConfigRequest,
        updated_by: str,
    ) -> tuple[ConfigResponse, UUID | None, str] | None:
        """Update a config by ID and return the prior cache identity."""
        query = select(self.model).where(self.model.id == config_id)
        result = await self.session.execute(query)
        config = result.scalar_one_or_none()
        if not config:
            return None

        old_org_id = config.organization_id
        old_key = config.key
        now = datetime.now(timezone.utc)

        effective_type = (
            request.type
            if request.type is not None
            else (
                ConfigType(config.config_type.value)
                if config.config_type
                else ConfigType.STRING
            )
        )

        if request.value is not None and request.value != "":
            stored_value = request.value
            if effective_type == ConfigType.SECRET:
                from src.core.security import encrypt_secret

                stored_value = encrypt_secret(request.value)
            config.value = {"value": stored_value}
        elif effective_type != ConfigType.SECRET and request.value is not None:
            config.value = {"value": request.value}

        if request.key is not None:
            config.key = request.key
        if request.type is not None:
            config.config_type = ConfigTypeEnum(request.type.value)
        if "description" in (request.model_fields_set or set()):
            config.description = request.description
        if "organization_id" in (request.model_fields_set or set()):
            config.organization_id = request.organization_id
        config.updated_at = now
        config.updated_by = updated_by
        await self.session.flush()
        await self.session.refresh(config)

        logger.info(
            f"Updated config {log_safe(config.key)} "
            f"(id={log_safe(config_id)}) org={log_safe(config.organization_id)}"
        )

        response_type = (
            ConfigType(config.config_type.value)
            if config.config_type
            else ConfigType.STRING
        )
        value = config.value.get("value") if isinstance(config.value, dict) else config.value
        response = ConfigResponse(
            id=config.id,
            key=config.key,
            value=value,
            type=response_type,
            scope="org" if config.organization_id else "GLOBAL",
            org_id=str(config.organization_id) if config.organization_id else None,
            description=config.description,
            updated_at=config.updated_at,
            updated_by=config.updated_by,
        )
        return response, old_org_id, old_key

    async def delete_config(self, config_id: UUID) -> ConfigModel | None:
        """Delete config by ID. Returns the deleted config or None if missing."""
        query = select(self.model).where(self.model.id == config_id)
        result = await self.session.execute(query)
        config = result.scalar_one_or_none()
        if not config:
            return None

        key = config.key
        await self.session.delete(config)
        await self.session.flush()

        logger.info(f"Deleted config {log_safe(key)} (id={log_safe(config_id)})")
        return config
