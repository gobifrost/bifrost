"""Persistence helpers for the Bifrost Codex Gateway."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from typing import Any, TypeGuard
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.security import encrypt_secret
from src.models.orm.codex_gateway import (
    CodexGatewayKey,
    CodexGatewayRequestLog,
    CodexGatewayUpstreamAccount,
)
from src.models.orm.users import User


SENSITIVE_METADATA_KEYS = {
    "content",
    "input",
    "messages",
    "output",
    "prompt",
    "response",
}

CODEX_GATEWAY_KEY_PREFIX = "bfck_"
CODEX_GATEWAY_KEY_MIN_LENGTH = len(CODEX_GATEWAY_KEY_PREFIX) + 32
CODEX_GATEWAY_KEY_MAX_LENGTH = 256
CODEX_GATEWAY_KEY_BODY_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
CODEX_GATEWAY_KEY_HASH_PREFIX = "sha256:"
MAX_ACTIVE_GATEWAY_KEYS_PER_USER = 10


def is_plausible_gateway_key(value: str | None) -> TypeGuard[str]:
    """Return whether a presented gateway key can match generated key material."""
    if value is None or not value.startswith(CODEX_GATEWAY_KEY_PREFIX):
        return False
    if not (CODEX_GATEWAY_KEY_MIN_LENGTH <= len(value) <= CODEX_GATEWAY_KEY_MAX_LENGTH):
        return False
    body = value[len(CODEX_GATEWAY_KEY_PREFIX) :]
    return bool(body) and all(char in CODEX_GATEWAY_KEY_BODY_CHARS for char in body)


@dataclass(frozen=True)
class CodexGatewayKeyMaterial:
    """Created gateway key record plus the one-time plaintext secret."""

    record: CodexGatewayKey
    plaintext_key: str


class CodexGatewayKeyLimitError(Exception):
    """Raised when a user has reached the active gateway key quota."""


class CodexGatewayRepository:
    """Repository for gateway keys, upstream accounts, and request logs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def generate_gateway_key() -> str:
        """Generate a downstream key with a recognizable Bifrost prefix."""
        return f"bfck_{secrets.token_urlsafe(32)}"

    @staticmethod
    def hash_gateway_key(plaintext_key: str) -> str:
        """Return the indexed keyed digest for a gateway key."""
        digest = hmac.new(
            get_settings().secret_key.encode("utf-8"),
            plaintext_key.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{CODEX_GATEWAY_KEY_HASH_PREFIX}{digest}"

    @staticmethod
    def verify_gateway_key(plaintext_key: str, key_hash: str) -> bool:
        """Verify a gateway key digest in constant time."""
        if not key_hash.startswith(CODEX_GATEWAY_KEY_HASH_PREFIX):
            return False
        return hmac.compare_digest(
            CodexGatewayRepository.hash_gateway_key(plaintext_key),
            key_hash,
        )

    async def create_gateway_key(
        self,
        *,
        user_id: UUID,
        name: str,
        project_id: UUID | None = None,
        allowed_models: list[str] | None = None,
        denied_models: list[str] | None = None,
        daily_limit: int | None = None,
        monthly_limit: int | None = None,
    ) -> CodexGatewayKeyMaterial:
        lock_result = await self.session.execute(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        if lock_result.scalar_one_or_none() is None:
            raise ValueError("Cannot create Codex Gateway key for unknown user")

        result = await self.session.execute(
            select(func.count())
            .select_from(CodexGatewayKey)
            .where(CodexGatewayKey.user_id == user_id)
            .where(CodexGatewayKey.status == "active")
            .where(CodexGatewayKey.revoked_at.is_(None))
        )
        active_key_count = int(result.scalar_one() or 0)
        if active_key_count >= MAX_ACTIVE_GATEWAY_KEYS_PER_USER:
            raise CodexGatewayKeyLimitError(
                f"At most {MAX_ACTIVE_GATEWAY_KEYS_PER_USER} active Codex Gateway keys are allowed per user"
            )

        plaintext_key = self.generate_gateway_key()
        record = CodexGatewayKey(
            user_id=user_id,
            project_id=project_id,
            key_hash=self.hash_gateway_key(plaintext_key),
            name=name,
            allowed_models=allowed_models or [],
            denied_models=denied_models or [],
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )
        self.session.add(record)
        await self.session.flush()
        await self.session.refresh(record)
        return CodexGatewayKeyMaterial(record=record, plaintext_key=plaintext_key)

    async def get_active_gateway_key_by_plaintext(
        self, plaintext_key: str
    ) -> CodexGatewayKey | None:
        if not is_plausible_gateway_key(plaintext_key):
            return None

        key_hash = self.hash_gateway_key(plaintext_key)
        result = await self.session.execute(
            select(CodexGatewayKey)
            .where(CodexGatewayKey.status == "active")
            .where(CodexGatewayKey.revoked_at.is_(None))
            .where(CodexGatewayKey.key_hash == key_hash)
        )
        candidate = result.scalar_one_or_none()
        if candidate is None:
            return None
        if candidate.status != "active" or candidate.revoked_at is not None:
            return None
        if not self.verify_gateway_key(plaintext_key, candidate.key_hash):
            return None
        return candidate

    async def list_gateway_keys_for_user(self, user_id: UUID) -> list[CodexGatewayKey]:
        result = await self.session.execute(
            select(CodexGatewayKey)
            .where(CodexGatewayKey.user_id == user_id)
            .order_by(CodexGatewayKey.created_at.desc(), CodexGatewayKey.id.desc())
        )
        return list(result.scalars().all())

    async def revoke_gateway_key_for_user(
        self,
        *,
        key_id: UUID,
        user_id: UUID,
    ) -> CodexGatewayKey | None:
        result = await self.session.execute(
            select(CodexGatewayKey)
            .where(CodexGatewayKey.id == key_id)
            .where(CodexGatewayKey.user_id == user_id)
        )
        key = result.scalar_one_or_none()
        if key is None:
            return None
        if key.revoked_at is None:
            key.status = "revoked"
            key.revoked_at = datetime.now(timezone.utc)
            await self.session.flush()
        return key

    async def get_active_upstream_account_for_user(
        self, user_id: UUID, provider: str = "chatgpt_codex"
    ) -> CodexGatewayUpstreamAccount | None:
        result = await self.session.execute(
            select(CodexGatewayUpstreamAccount)
            .where(CodexGatewayUpstreamAccount.user_id == user_id)
            .where(CodexGatewayUpstreamAccount.provider == provider)
            .where(CodexGatewayUpstreamAccount.revoked_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def create_upstream_account(
        self,
        *,
        user_id: UUID,
        upstream_subject: str,
        upstream_email: str | None = None,
        upstream_workspace_id: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        access_token_expires_at: Any = None,
        scopes: list[str] | None = None,
        provider: str = "chatgpt_codex",
    ) -> CodexGatewayUpstreamAccount:
        account = CodexGatewayUpstreamAccount(
            user_id=user_id,
            provider=provider,
            upstream_subject=upstream_subject,
            upstream_email=upstream_email,
            upstream_workspace_id=upstream_workspace_id,
            encrypted_access_token=(
                encrypt_secret(access_token) if access_token is not None else None
            ),
            encrypted_refresh_token=(
                encrypt_secret(refresh_token) if refresh_token is not None else None
            ),
            access_token_expires_at=access_token_expires_at,
            scopes=scopes or [],
        )
        self.session.add(account)
        await self.session.flush()
        await self.session.refresh(account)
        return account

    async def upsert_upstream_account_for_user(
        self,
        *,
        user_id: UUID,
        upstream_subject: str,
        upstream_email: str | None = None,
        upstream_workspace_id: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        access_token_expires_at: Any = None,
        scopes: list[str] | None = None,
        provider: str = "chatgpt_codex",
    ) -> CodexGatewayUpstreamAccount:
        result = await self.session.execute(
            select(CodexGatewayUpstreamAccount)
            .where(CodexGatewayUpstreamAccount.user_id == user_id)
            .where(CodexGatewayUpstreamAccount.provider == provider)
            .where(CodexGatewayUpstreamAccount.revoked_at.is_(None))
        )
        account = result.scalar_one_or_none()
        if account is None:
            return await self.create_upstream_account(
                user_id=user_id,
                upstream_subject=upstream_subject,
                upstream_email=upstream_email,
                upstream_workspace_id=upstream_workspace_id,
                access_token=access_token,
                refresh_token=refresh_token,
                access_token_expires_at=access_token_expires_at,
                scopes=scopes,
                provider=provider,
            )

        account.upstream_subject = upstream_subject
        account.upstream_email = upstream_email
        account.upstream_workspace_id = upstream_workspace_id
        account.access_token_expires_at = access_token_expires_at
        account.scopes = scopes or []
        if access_token is not None:
            account.encrypted_access_token = encrypt_secret(access_token)
        if refresh_token is not None:
            account.encrypted_refresh_token = encrypt_secret(refresh_token)
        account.last_refresh_at = datetime.now(timezone.utc)
        await self.session.flush()
        return account

    async def revoke_upstream_account_for_user(
        self,
        *,
        user_id: UUID,
        provider: str = "chatgpt_codex",
    ) -> CodexGatewayUpstreamAccount | None:
        result = await self.session.execute(
            select(CodexGatewayUpstreamAccount)
            .where(CodexGatewayUpstreamAccount.user_id == user_id)
            .where(CodexGatewayUpstreamAccount.provider == provider)
            .where(CodexGatewayUpstreamAccount.revoked_at.is_(None))
        )
        account = result.scalar_one_or_none()
        if account is None:
            return None
        account.revoked_at = datetime.now(timezone.utc)
        await self.session.flush()
        return account

    async def create_request_log(
        self,
        *,
        request_id: str,
        endpoint: str,
        status_code: int,
        policy_decision: str,
        user_id: UUID | None = None,
        project_id: UUID | None = None,
        gateway_key_id: UUID | None = None,
        oauth_account_id: UUID | None = None,
        model: str | None = None,
        streaming: bool = False,
        provider_error_code: str | None = None,
        input_token_count: int | None = None,
        output_token_count: int | None = None,
        latency_ms: int | None = None,
        denied_reason: str | None = None,
        source_ip: str | None = None,
        client_user_agent: str | None = None,
        request_metadata: dict[str, Any] | None = None,
        captured_prompt: str | None = None,
        captured_response: str | None = None,
        capture_sensitive_payloads: bool = False,
    ) -> CodexGatewayRequestLog:
        safe_metadata = self._redact_sensitive_metadata(request_metadata or {})
        log = CodexGatewayRequestLog(
            request_id=request_id,
            user_id=user_id,
            project_id=project_id,
            gateway_key_id=gateway_key_id,
            oauth_account_id=oauth_account_id,
            endpoint=endpoint,
            model=model,
            streaming=streaming,
            status_code=status_code,
            provider_error_code=provider_error_code,
            input_token_count=input_token_count,
            output_token_count=output_token_count,
            latency_ms=latency_ms,
            policy_decision=policy_decision,
            denied_reason=denied_reason,
            source_ip=source_ip,
            client_user_agent=client_user_agent,
            request_metadata=safe_metadata,
            captured_prompt=captured_prompt if capture_sensitive_payloads else None,
            captured_response=captured_response if capture_sensitive_payloads else None,
        )
        self.session.add(log)
        await self.session.flush()
        await self.session.refresh(log)
        return log

    @staticmethod
    def _redact_sensitive_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in metadata.items()
            if key.lower() not in SENSITIVE_METADATA_KEYS
        }
