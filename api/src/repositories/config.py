"""Config repository."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select

from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType
from src.core.security import SECRET_PLACEHOLDER
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
from src.services.authorization.reach import OrgReach

logger = logging.getLogger(__name__)


class RequiredConfigUnset(RuntimeError):
    """Raised when a required config key has no value set.

    Provides an actionable message naming the key and telling the operator how
    to fix it.  Call ``repo.require(key)`` instead of ``repo.get_config(key)``
    wherever a missing value should be a hard failure rather than a silent None.
    """

    def __init__(self, key: str) -> None:
        super().__init__(
            f"Required config '{key}' is not set. "
            f"Set it with `bifrost configs set {key} --value <value>` "
            f"or in the solution's Setup tab."
        )


@dataclass(frozen=True)
class ConfigWrite:
    """A config write's masked response plus the value the cache must store."""

    response: ConfigResponse
    # As persisted: ciphertext for secrets, never returned to a caller.
    stored_value: Any


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
                SECRET_PLACEHOLDER
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
                    required=config.required,
                    position=config.position,
                    updated_at=config.updated_at,
                    updated_by=config.updated_by,
                )
            )
        return schemas

    async def list_configs_in_reach(
        self,
        reach: OrgReach,
        *,
        include_global_fallback: bool,
    ) -> list[ConfigResponse]:
        """List rows authorized before query execution and response shaping.

        Global configuration is a shared fallback for an authorized
        organization read.  It is included here only because the caller has
        at least one authorized organization; explicit Global targeting is
        decided by the router before it calls this method.
        """
        query = select(self.model, Integration.name.label("integration_name")).outerjoin(
            Integration,
            and_(self.model.integration_id == Integration.id, Integration.is_deleted.is_(False)),
        )
        predicate = reach.where(self.model.organization_id)
        if predicate is not None:
            if include_global_fallback and not reach.covers(None):
                predicate = or_(predicate, self.model.organization_id.is_(None))
            query = query.where(predicate)
        query = query.order_by(self.model.key)
        rows = (await self.session.execute(query)).all()
        return [self._to_response(row[0], integration_name=row[1]) for row in rows]

    async def get_config(self, key: str) -> ConfigModel | None:
        """Get config by key with cascade scoping: org-specific > global."""
        return await self.get(key=key)

    @staticmethod
    def _to_response(
        config: ConfigModel, *, integration_name: str | None
    ) -> ConfigResponse:
        """Shape one row into a ``ConfigResponse``.

        Unwraps the ``{"value": X}`` JSONB storage envelope and masks
        ``secret``-type values, so no read surface built on this can leak a
        stored secret.
        """
        raw_value = (
            config.value.get("value")
            if isinstance(config.value, dict)
            else config.value
        )
        display_value = (
            SECRET_PLACEHOLDER
            if config.config_type == ConfigTypeEnum.SECRET
            else raw_value
        )
        return ConfigResponse(
            id=config.id,
            key=config.key,
            value=display_value,
            type=ConfigType(config.config_type.value)
            if config.config_type
            else ConfigType.STRING,
            scope="org" if config.organization_id else "GLOBAL",
            org_id=str(config.organization_id) if config.organization_id else None,
            integration_id=str(config.integration_id)
            if config.integration_id
            else None,
            integration_name=integration_name,
            description=config.description,
            required=config.required,
            position=config.position,
            updated_at=config.updated_at,
            updated_by=config.updated_by,
        )

    async def get_config_by_id(self, config_id: UUID) -> ConfigResponse | None:
        """Get one config by UUID, or ``None`` when it does not exist.

        No org cascade: IDs are globally unique, so an ID lookup resolves
        directly. This deliberately matches :meth:`update_config_by_id` and
        :meth:`delete_config`, which also filter on ``id`` alone. The router
        authorizes the persisted organization before returning or mutating the
        row. Secret values are masked by :meth:`_to_response`.
        """
        query = (
            select(self.model, Integration.name.label("integration_name"))
            .outerjoin(
                Integration,
                and_(
                    self.model.integration_id == Integration.id,
                    Integration.is_deleted.is_(False),
                ),
            )
            .where(self.model.id == config_id)
        )

        row = (await self.session.execute(query)).first()
        if row is None:
            return None
        return self._to_response(row[0], integration_name=row[1])

    async def require(self, key: str) -> ConfigModel:
        """Get a config by key, raising RequiredConfigUnset when it is absent.

        Use this wherever a missing value should be a hard, actionable failure
        rather than a silent None.  The existing ``get_config`` method is
        unchanged so callers that tolerate None are unaffected.
        """
        config = await self.get_config(key)
        if config is None:
            raise RequiredConfigUnset(key)
        return config

    async def merged_for_sdk(self, *, external: bool | None = None) -> dict[str, Any]:
        """Return the full merged config dict for this scope.

        ``external`` (EXT-1 NEW-1): when the calling principal is a direct,
        EXTERNAL (non-bypass) USER — e.g. a portal user hitting ``bifrost config
        get`` / ``/api/sdk/config/get`` directly — drop the global
        (organization_id IS NULL) tier so they can't read (nor the endpoint
        decrypt) a global secret. Defaults to ``self.is_external``.

        SDK services derive this restriction from the current Caller.
        Workflow powers retain the shared cascade; named config and Secrets
        permissions still decide whether values may be read or decrypted.

        External reads BYPASS the cache entirely — they're a rare,
        portal-admin-only path, and a separate cache namespace would add
        invalidation complexity for no benefit. Only the (cached) org+global
        view is ever stored under the org key.
        """
        if external is None:
            external = self.is_external
        from src.core.cache.keys import (
            CONFIG_CACHE_COMPLETE_FIELD,
            TTL_CONFIG,
            config_cache_field,
            config_hash_key_versioned,
        )
        from src.core.cache.redis_client import get_shared_redis

        org_id_str = str(self.org_id) if self.org_id is not None else None
        # An external principal with no org has no readable config at all.
        if external and self.org_id is None:
            return {}

        cache = None
        cache_hash_key = None
        # External reads skip the cache (the cached org key holds the
        # global-merged view — reusing it would re-leak global).
        if not external:
            try:
                redis = await get_shared_redis()
                hash_key = await config_hash_key_versioned(redis, org_id_str)
                cached = await redis.hgetall(hash_key)  # type: ignore[misc]
                complete = cached.get(CONFIG_CACHE_COMPLETE_FIELD)
                if complete is None:
                    complete = cached.get(CONFIG_CACHE_COMPLETE_FIELD.encode())
                if complete in ("1", b"1"):
                    return self._decode_cached_config(cached)
                cache = redis
                cache_hash_key = hash_key
            except Exception as e:
                logger.warning(f"Config cache read failed: {e}")

        config_dict: dict[str, Any] = {}

        # Global tier — SKIPPED for an external caller (no global scope).
        if not external:
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

        # Only the org+global (non-external) view is cached under the org key.
        # The completion field shares this one hash with JSON-encoded user
        # fields. That makes an empty view cacheable in one atomic Redis value
        # without a marker that can outlive its data.
        if cache is not None and cache_hash_key is not None:
            try:
                # Do not fill a generation that changed while SQL was running.
                # A concurrent write made the observed snapshot unreachable.
                if (
                    await config_hash_key_versioned(cache, org_id_str)
                    == cache_hash_key
                ):
                    pipe = cache.pipeline(transaction=True)
                    pipe.delete(cache_hash_key)
                    pipe.hset(
                        cache_hash_key,
                        mapping={
                            CONFIG_CACHE_COMPLETE_FIELD: "1",
                            **{
                                config_cache_field(key): json.dumps(value)
                                for key, value in config_dict.items()
                            },
                        },
                    )
                    pipe.expire(cache_hash_key, TTL_CONFIG)
                    await pipe.execute()
            except Exception as e:
                logger.warning(f"Config cache write failed: {e}")

        logger.debug(
            f"Loaded {len(config_dict)} config entries for org={log_safe(org_id_str)}"
        )
        return config_dict

    @staticmethod
    def _decode_cached_config(cached: dict[Any, Any]) -> dict[str, Any]:
        """Decode a complete Redis config hash without exposing cache metadata."""
        out: dict[str, Any] = {}
        from src.core.cache.keys import CONFIG_CACHE_COMPLETE_FIELD

        for key, value in cached.items():
            field = key.decode() if isinstance(key, bytes) else key
            if not isinstance(field, str):
                raise ValueError("Config cache field is not a string")
            if field == CONFIG_CACHE_COMPLETE_FIELD:
                continue
            value_str = value.decode() if isinstance(value, bytes) else value
            try:
                key_str = json.loads(field)
                if not isinstance(key_str, str):
                    raise ValueError("Config cache user field is not a JSON string")
                entry = json.loads(value_str)
                if not isinstance(entry, dict):
                    raise ValueError("Config cache value is not an object")
                out[key_str] = entry
            except (TypeError, json.JSONDecodeError) as e:
                raise ValueError("Malformed config cache entry") from e
        return out

    @staticmethod
    def _sdk_entry(config: ConfigModel) -> dict[str, Any]:
        value = config.value.get("value") if isinstance(config.value, dict) else config.value
        return {
            "value": value,
            "type": config.config_type.value if config.config_type else "string",
        }

    async def get_for_sdk(
        self,
        key: str,
        *,
        external: bool | None = None,
    ) -> dict[str, Any] | None:
        """Return one SDK config entry without materializing the full view.

        A warm complete view uses one ``HMGET`` for the completion field and
        exactly the requested user field.
        A cold internal read builds the existing complete merged view once;
        that gives both positive and missing keys a durable cached answer
        without ever HSET-ing a partial view. External and cache-failure
        reads use narrow database queries and never share this cache.
        """
        if external is None:
            external = self.is_external
        if external and self.org_id is None:
            return None

        if not external:
            from src.core.cache.keys import (
                CONFIG_CACHE_COMPLETE_FIELD,
                config_cache_field,
                config_hash_key_versioned,
            )
            from src.core.cache.redis_client import get_shared_redis

            cache_cold = False
            try:
                redis = await get_shared_redis()
                hash_key = await config_hash_key_versioned(
                    redis, str(self.org_id) if self.org_id is not None else None
                )
                complete, cached = await redis.hmget(  # type: ignore[misc]
                    hash_key,
                    [CONFIG_CACHE_COMPLETE_FIELD, config_cache_field(key)],
                )
                if complete in ("1", b"1"):
                    if cached is None:
                        return None
                    return self._decode_cached_config(
                        {config_cache_field(key): cached}
                    )[key]

                cache_cold = True
            except Exception as e:
                logger.warning(f"Config cache keyed read failed: {e}")

            if cache_cold:
                # A cold internal keyed read must populate the shared complete
                # view once. Otherwise a repeatedly missing key would repeat
                # its database query forever. Keep this outside the Redis
                # boundary so SQL failures retain their original error and
                # are never retried as a cache fallback.
                return (await self.merged_for_sdk(external=False)).get(key)

        global_config = None
        if not external:
            global_result = await self.session.execute(
                select(self.model).where(
                    self.model.key == key,
                    self.model.organization_id.is_(None),
                    self.model.integration_id.is_(None),
                )
            )
            global_config = global_result.scalar_one_or_none()

        if self.org_id is not None:
            org_result = await self.session.execute(
                select(self.model).where(
                    self.model.key == key,
                    self.model.organization_id == self.org_id,
                    self.model.integration_id.is_(None),
                )
            )
            org_config = org_result.scalar_one_or_none()
            if org_config is not None:
                return self._sdk_entry(org_config)

        return self._sdk_entry(global_config) if global_config is not None else None

    async def get_config_strict(self, key: str) -> ConfigModel | None:
        """Get a config strictly in current org scope."""
        query = select(self.model).where(
            self.model.key == key,
            self.model.organization_id == self.org_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def set_config(
        self, request: SetConfigRequest, updated_by: str
    ) -> ConfigWrite:
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

        return ConfigWrite(
            response=self._to_response(config, integration_name=None),
            stored_value=stored_value,
        )

    async def update_config_by_id(
        self,
        config_id: UUID,
        request: UpdateConfigRequest,
        updated_by: str,
    ) -> tuple[ConfigWrite, UUID | None, str] | None:
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

        stored_value = config.value.get("value") if isinstance(config.value, dict) else config.value
        write = ConfigWrite(
            response=self._to_response(config, integration_name=None),
            stored_value=stored_value,
        )
        return write, old_org_id, old_key

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
