"""Provider configuration for sandboxed builder runners."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import decrypt_secret, encrypt_secret
from src.models.contracts.sandbox_runner import (
    DEFAULT_CLOUDFLARE_SCRIPT_NAME,
    DEFAULT_CLOUDFLARE_WORKFLOW_NAME,
    SandboxRunnerBlocker,
    SandboxRunnerCloudflarePublic,
    SandboxRunnerConfigPublic,
    SandboxRunnerConfigSave,
    SandboxRunnerLocalPublic,
    SandboxRunnerReadiness,
    SandboxRunnerStoredConfig,
)
from src.models.orm import SystemConfig

SANDBOX_RUNNER_CONFIG_CATEGORY = "sandbox_runner"
SANDBOX_RUNNER_CONFIG_KEY = "provider_config"


class SandboxRunnerConfigService:
    """Manage global sandbox runner provider configuration in SystemConfig."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_config(self) -> SandboxRunnerConfigPublic | None:
        """Return the current config with all secrets masked."""
        stored = await self._get_stored_config()
        if stored is None:
            return None
        return self._to_public(stored)

    async def get_internal_config(self) -> SandboxRunnerStoredConfig | None:
        """Return the stored encrypted payload for internal control-plane use."""
        return await self._get_stored_config()

    async def get_decrypted_internal_config(self) -> dict[str, Any] | None:
        """Return the internal config with provider secrets decrypted."""
        stored = await self._get_stored_config()
        if stored is None:
            return None

        data = stored.model_dump()
        cloudflare = dict(data.get("cloudflare") or {})
        if encrypted := cloudflare.pop("encrypted_api_token", None):
            cloudflare["api_token"] = decrypt_secret(str(encrypted))
        data["cloudflare"] = cloudflare or None

        local = dict(data.get("local") or {})
        if encrypted := local.pop("encrypted_runner_secret", None):
            local["runner_secret"] = decrypt_secret(str(encrypted))
        data["local"] = local or None

        return data

    async def save_config(
        self,
        request: SandboxRunnerConfigSave,
        *,
        updated_by: str = "system",
    ) -> SandboxRunnerConfigPublic:
        """Save provider configuration, preserving omitted existing secrets."""
        existing_row = await self._get_row()
        existing = self._parse_row(existing_row)

        data: dict[str, Any] = {
            "provider": request.provider,
            "enabled": request.enabled,
            "callback_base_url": request.callback_base_url,
            "provisioned": False,
            "connected": False,
            "cloudflare": None,
            "local": None,
        }

        if request.provider == "cloudflare":
            data["cloudflare"] = self._build_cloudflare_payload(request, existing)
        else:
            data["local"] = self._build_local_payload(request, existing)

        if request.enabled and not request.callback_base_url:
            raise ValueError("callback_base_url is required to enable the sandbox runner")
        if (
            request.provider == "cloudflare"
            and request.callback_base_url
            and urlsplit(request.callback_base_url).scheme != "https"
        ):
            raise ValueError(
                "Cloudflare requires an HTTPS Bifrost callback URL; no extra "
                "hostname or public port is required"
            )

        if existing is not None and not self._connection_settings_changed(
            request,
            existing,
            data,
        ):
            data["provisioned"] = existing.provisioned
            data["connected"] = existing.connected

        if existing_row is None:
            existing_row = SystemConfig(
                id=uuid4(),
                category=SANDBOX_RUNNER_CONFIG_CATEGORY,
                key=SANDBOX_RUNNER_CONFIG_KEY,
                value_json=data,
                value_bytes=None,
                organization_id=None,
                created_by=updated_by,
                updated_by=updated_by,
            )
            self.session.add(existing_row)
        else:
            existing_row.value_json = data
            existing_row.updated_at = datetime.now(timezone.utc)
            existing_row.updated_by = updated_by

        await self.session.flush()
        return self._to_public(SandboxRunnerStoredConfig.model_validate(data))

    async def set_runtime_status(
        self,
        *,
        provisioned: bool,
        connected: bool,
        updated_by: str = "system",
    ) -> SandboxRunnerConfigPublic:
        """Persist status proven by control-plane provisioning and probes."""
        if connected and not provisioned:
            raise ValueError("A connected sandbox runner must be provisioned")
        row = await self._get_row()
        stored = self._parse_row(row)
        if row is None or stored is None:
            raise LookupError("Sandbox runner configuration does not exist")
        data = stored.model_dump()
        data["provisioned"] = provisioned
        data["connected"] = connected
        row.value_json = data
        row.updated_at = datetime.now(timezone.utc)
        row.updated_by = updated_by
        await self.session.flush()
        return self._to_public(SandboxRunnerStoredConfig.model_validate(data))

    async def delete_config(self) -> bool:
        """Delete the global runner configuration if it exists."""
        row = await self._get_row()
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    async def get_readiness(self, *, ai_configured: bool) -> SandboxRunnerReadiness:
        """Return admin-facing readiness checks for enabling Builder."""
        stored = await self._get_stored_config()
        blockers: list[SandboxRunnerBlocker] = []

        if not ai_configured:
            blockers.append(
                SandboxRunnerBlocker(
                    code="ai_not_configured",
                    message="AI provider configuration is required before Builder can run.",
                    action="Configure the AI provider and builder model.",
                )
            )

        if stored is None:
            blockers.append(
                SandboxRunnerBlocker(
                    code="provider_not_configured",
                    message="No sandbox runner provider has been configured.",
                    action="Choose Cloudflare or Local and save provider settings.",
                )
            )
            return SandboxRunnerReadiness(
                configured=False,
                ready=False,
                ai_configured=ai_configured,
                blockers=blockers,
            )

        credentials_configured = self._credentials_configured(stored)
        if not credentials_configured:
            blockers.append(
                SandboxRunnerBlocker(
                    code="credentials_missing",
                    message="Sandbox runner credentials are missing.",
                    action="Save provider credentials.",
                )
            )

        callback_configured = bool(stored.callback_base_url)
        if not callback_configured:
            blockers.append(
                SandboxRunnerBlocker(
                    code="callback_missing",
                    message="A callback base URL is required so external jobs can report progress and results.",
                    action="Save the public Bifrost URL that runners can reach.",
                )
            )

        if not stored.provisioned:
            blockers.append(
                SandboxRunnerBlocker(
                    code="not_provisioned",
                    message="The sandbox runner has not been provisioned.",
                    action="Run provisioning from Builder setup.",
                )
            )

        if not stored.connected:
            blockers.append(
                SandboxRunnerBlocker(
                    code="not_connected",
                    message="The sandbox runner has not passed a live connectivity check.",
                    action="Run the connection test.",
                )
            )

        if not stored.enabled:
            blockers.append(
                SandboxRunnerBlocker(
                    code="not_enabled",
                    message="Builder is not enabled for users.",
                    action="Enable Builder after the checks pass.",
                )
            )

        ready = (
            ai_configured
            and credentials_configured
            and callback_configured
            and stored.provisioned
            and stored.connected
            and stored.enabled
        )

        return SandboxRunnerReadiness(
            configured=True,
            ready=ready,
            ai_configured=ai_configured,
            provider=stored.provider,
            enabled=stored.enabled,
            credentials_configured=credentials_configured,
            callback_configured=callback_configured,
            provisioned=stored.provisioned,
            connected=stored.connected,
            blockers=blockers,
        )

    async def is_dispatch_ready(self) -> bool:
        """Return whether non-AI sandbox work may be dispatched safely."""
        stored = await self._get_stored_config()
        return bool(
            stored
            and stored.enabled
            and stored.callback_base_url
            and stored.provisioned
            and stored.connected
            and self._credentials_configured(stored)
        )

    async def _get_row(self) -> SystemConfig | None:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == SANDBOX_RUNNER_CONFIG_CATEGORY,
                SystemConfig.key == SANDBOX_RUNNER_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        return result.scalars().first()

    async def _get_stored_config(self) -> SandboxRunnerStoredConfig | None:
        return self._parse_row(await self._get_row())

    def _parse_row(self, row: SystemConfig | None) -> SandboxRunnerStoredConfig | None:
        if row is None or not row.value_json:
            return None
        return SandboxRunnerStoredConfig.model_validate(row.value_json)

    def _build_cloudflare_payload(
        self,
        request: SandboxRunnerConfigSave,
        existing: SandboxRunnerStoredConfig | None,
    ) -> dict[str, object]:
        cloudflare = request.cloudflare
        existing_cloudflare = (
            existing.cloudflare
            if existing and existing.provider == "cloudflare"
            else None
        )
        encrypted_token = None
        if cloudflare and cloudflare.api_token:
            encrypted_token = encrypt_secret(cloudflare.api_token)
        elif existing_cloudflare:
            encrypted_token = existing_cloudflare.get("encrypted_api_token")

        return {
            "account_id": cloudflare.account_id if cloudflare else None,
            "encrypted_api_token": encrypted_token,
            "script_name": (
                cloudflare.script_name
                if cloudflare and cloudflare.script_name
                else DEFAULT_CLOUDFLARE_SCRIPT_NAME
            ),
            "workflow_name": (
                cloudflare.workflow_name
                if cloudflare and cloudflare.workflow_name
                else DEFAULT_CLOUDFLARE_WORKFLOW_NAME
            ),
        }

    def _build_local_payload(
        self,
        request: SandboxRunnerConfigSave,
        existing: SandboxRunnerStoredConfig | None,
    ) -> dict[str, object]:
        local = request.local
        existing_local = (
            existing.local if existing and existing.provider == "local" else None
        )
        encrypted_secret = None
        if local and local.runner_secret:
            encrypted_secret = encrypt_secret(local.runner_secret)
        elif existing_local and existing_local.get("encrypted_runner_secret"):
            encrypted_secret = existing_local["encrypted_runner_secret"]
        elif local and local.endpoint_url:
            encrypted_secret = encrypt_secret(secrets.token_urlsafe(32))

        return {
            "endpoint_url": local.endpoint_url if local else None,
            "encrypted_runner_secret": encrypted_secret,
        }

    def _to_public(self, stored: SandboxRunnerStoredConfig) -> SandboxRunnerConfigPublic:
        cloudflare = None
        if stored.provider == "cloudflare":
            cloudflare_data = stored.cloudflare or {}
            cloudflare = SandboxRunnerCloudflarePublic(
                account_id=_optional_str(cloudflare_data.get("account_id")),
                api_token_set=bool(cloudflare_data.get("encrypted_api_token")),
                script_name=str(
                    cloudflare_data.get("script_name") or DEFAULT_CLOUDFLARE_SCRIPT_NAME
                ),
                workflow_name=str(
                    cloudflare_data.get("workflow_name") or DEFAULT_CLOUDFLARE_WORKFLOW_NAME
                ),
            )

        local = None
        if stored.provider == "local":
            local_data = stored.local or {}
            local = SandboxRunnerLocalPublic(
                endpoint_url=_optional_str(local_data.get("endpoint_url")),
                runner_secret_set=bool(local_data.get("encrypted_runner_secret")),
            )

        return SandboxRunnerConfigPublic(
            provider=stored.provider,
            enabled=stored.enabled,
            callback_base_url=stored.callback_base_url,
            provisioned=stored.provisioned,
            connected=stored.connected,
            cloudflare=cloudflare,
            local=local,
        )

    def _credentials_configured(self, stored: SandboxRunnerStoredConfig) -> bool:
        if stored.provider == "cloudflare":
            cloudflare = stored.cloudflare or {}
            return bool(
                cloudflare.get("account_id")
                and cloudflare.get("encrypted_api_token")
            )
        local = stored.local or {}
        return bool(local.get("endpoint_url") and local.get("encrypted_runner_secret"))

    def _connection_settings_changed(
        self,
        request: SandboxRunnerConfigSave,
        existing: SandboxRunnerStoredConfig,
        new_data: dict[str, Any],
    ) -> bool:
        if existing.provider != request.provider:
            return True
        if existing.callback_base_url != request.callback_base_url:
            return True
        if request.provider == "cloudflare":
            if request.cloudflare and request.cloudflare.api_token:
                return True
            old = existing.cloudflare or {}
            new = new_data.get("cloudflare") or {}
            return any(
                old.get(key) != new.get(key)
                for key in ("account_id", "script_name", "workflow_name")
            )
        if request.local and request.local.runner_secret:
            return True
        old = existing.local or {}
        new = new_data.get("local") or {}
        return old.get("endpoint_url") != new.get("endpoint_url")


async def get_builder_readiness(
    session: AsyncSession,
) -> tuple[bool, SandboxRunnerReadiness]:
    """Return canonical AI + sandbox readiness for every Builder surface."""
    from src.services.llm_config_service import LLMConfigService

    llm_config = await LLMConfigService(session).get_config()
    ai_configured = bool(
        llm_config
        and llm_config.is_configured
        and llm_config.api_key_set
        and (llm_config.builder_model or llm_config.model)
    )
    readiness = await SandboxRunnerConfigService(session).get_readiness(
        ai_configured=ai_configured
    )
    return ai_configured, readiness


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["SandboxRunnerConfigService", "get_builder_readiness"]
