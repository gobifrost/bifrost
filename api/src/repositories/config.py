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
        include_orphaned: bool = False,
    ) -> list[ConfigResponse]:
        """List configs with specified filter type.

        Orphaned configs (former-install data; orphaned_at stamped) are hidden by
        default and only surfaced when ``include_orphaned`` is True.
        """
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

        if not include_orphaned:
            query = query.where(self.model.orphaned_at.is_(None))

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
                    orphaned_at=config.orphaned_at,
                    origin_solution_slug=config.origin_solution_slug,
                )
            )
        return schemas

    async def get_config(self, key: str) -> ConfigModel | None:
        """Get config by key with cascade scoping: org-specific > global."""
        return await self.get(key=key)

    async def merged_for_sdk(self, *, external: bool | None = None) -> dict[str, Any]:
        """Return the full merged config dict for this scope.

        ``external`` (EXT-1 NEW-1): when the calling principal is a direct,
        EXTERNAL (non-bypass) USER — e.g. a portal user hitting ``bifrost config
        get`` / ``/api/sdk/config/get`` directly — drop the global
        (organization_id IS NULL) tier so they can't read (nor the endpoint
        decrypt) a global secret. Defaults to ``self.is_external``.

        This is the USER-facing restriction ONLY. The ENGINE/sentinel path
        (workflow execution, embed-app sessions — ``is_superuser`` sentinel,
        ``is_external`` False) keeps the full org+global merge UNCHANGED: a
        workflow legitimately reads global config, and an external user's whole
        portal runs on those workflows. The CLI/SDK endpoints distinguish by
        passing ``external=current_user.is_external`` (False for the sentinel).

        External reads BYPASS the cache entirely — they're a rare,
        portal-admin-only path, and a separate cache namespace would add
        invalidation complexity for no benefit. Only the (cached) org+global
        view is ever stored under the org key.
        """
        if external is None:
            external = self.is_external
        from src.core.cache.keys import (
            TTL_CONFIG,
            config_hash_key_versioned,
        )
        from src.core.cache.redis_client import get_shared_redis

        org_id_str = str(self.org_id) if self.org_id is not None else None
        # An external principal with no org has no readable config at all.
        if external and self.org_id is None:
            return {}

        # External reads skip the cache (the cached org key holds the
        # global-merged view — reusing it would re-leak global).
        if not external:
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

        # Global tier — SKIPPED for an external caller (no global scope).
        if not external:
            global_q = select(self.model).where(
                self.model.organization_id.is_(None),
                self.model.integration_id.is_(None),
                # Orphaned values (former-install data) are unreachable at
                # runtime — invisible until the Solution is reinstalled/
                # reattached, matching the table invariant.
                self.model.orphaned_at.is_(None),
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
                self.model.orphaned_at.is_(None),
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

        # Only the org+global (non-external) view is cached under the org key.
        if config_dict and not external:
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
        """Get a LIVE config strictly in current org scope.

        Orphaned values (former-install data) are unreachable at runtime — they
        are not returned here, matching the table invariant (invisible until
        reattach). The orphaned row is healed back to live by ``set_config``
        when the operator re-enters the value in scope.
        """
        query = select(self.model).where(
            self.model.key == key,
            self.model.organization_id == self.org_id,
            self.model.orphaned_at.is_(None),
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

        # Upsert by the unique natural key (integration_id IS NULL, org, key).
        # This deliberately matches BOTH live and ORPHANED rows: re-entering a
        # value for a key whose former-install value was orphaned heals that row
        # back to live (clears orphan provenance) rather than colliding with the
        # unique index on insert.
        existing_q = select(self.model).where(
            self.model.key == request.key,
            self.model.organization_id == self.org_id,
            self.model.integration_id.is_(None),
        )
        existing = (await self.session.execute(existing_q)).scalar_one_or_none()

        if existing:
            existing.value = {"value": stored_value}
            existing.config_type = db_config_type
            existing.description = request.description
            existing.updated_at = now
            existing.updated_by = updated_by
            # Heal an orphaned value: an explicit re-set in scope makes it live.
            existing.orphaned_at = None
            existing.origin_solution_slug = None
            existing.origin_solution_id = None
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
