"""Provider configuration for sandboxed builder runners."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import Any
from uuid import uuid4

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
            "provisioned": request.provisioned,
            "connected": request.connected,
            "cloudflare": None,
            "local": None,
        }

        if request.provider == "cloudflare":
            data["cloudflare"] = self._build_cloudflare_payload(request, existing)
        else:
            data["local"] = self._build_local_payload(request, existing)

        if request.enabled and not request.callback_base_url:
            raise ValueError("callback_base_url is required to enable the sandbox runner")

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
        existing_cloudflare = existing.cloudflare if existing and existing.provider == "cloudflare" else None
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
        existing_local = existing.local if existing and existing.provider == "local" else None
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
                account_id=cloudflare_data.get("account_id") or None,
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
                endpoint_url=local_data.get("endpoint_url") or None,
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
            return bool(cloudflare.get("encrypted_api_token"))
        local = stored.local or {}
        return bool(local.get("endpoint_url") and local.get("encrypted_runner_secret"))

