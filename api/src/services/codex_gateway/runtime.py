"""Runtime orchestration for the Bifrost Codex Gateway."""

from __future__ import annotations

from asyncio import TimeoutError as AsyncioTimeoutError
from asyncio import sleep, wait_for
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy.exc import MultipleResultsFound

from src.core.security import decrypt_secret
from src.models.contracts.codex_gateway import (
    CodexGatewayKeyContext,
    CodexGatewayKeyStatus,
    CodexGatewayRequestContext,
    CodexGatewayUpstreamAccount as CodexGatewayUpstreamAccountContext,
    OpenAICompatibleError,
)
from src.models.orm.codex_gateway import (
    CodexGatewayKey,
    CodexGatewayUpstreamAccount,
)
from src.repositories.codex_gateway import (
    CodexGatewayRepository,
    is_plausible_gateway_key,
)
from src.services.codex_gateway.policy import CodexGatewayPolicyEngine


CODEX_GATEWAY_KEY_HEADER = "X-Bifrost-Codex-Key"
CODEX_GATEWAY_RESPONSES_ENDPOINT = "/v1/responses"
CODEX_GATEWAY_UPSTREAM_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class CodexGatewayUpstreamResponse:
    """Provider response normalized for gateway logging and transport."""

    status_code: int
    body: dict[str, Any]
    provider_error_code: str | None = None
    input_token_count: int | None = None
    output_token_count: int | None = None


class CodexGatewayUpstreamClient(Protocol):
    async def create_response(
        self, *, access_token: str, payload: dict[str, Any]
    ) -> CodexGatewayUpstreamResponse:
        """Dispatch a Responses API request with a user-scoped upstream token."""
        raise NotImplementedError


class UnconfiguredCodexGatewayUpstreamClient:
    """Placeholder upstream client until the subscription backend is pinned down."""

    async def create_response(
        self, *, access_token: str, payload: dict[str, Any]
    ) -> CodexGatewayUpstreamResponse:
        await sleep(0)
        configured_model = payload.get("model") if access_token else None
        return CodexGatewayUpstreamResponse(
            status_code=502,
            provider_error_code="upstream_not_configured",
            body={
                "error": {
                    "message": "The upstream ChatGPT/Codex transport is not configured.",
                    "type": "server_error",
                    "param": "model" if configured_model else None,
                    "code": "upstream_not_configured",
                }
            },
        )


@dataclass(frozen=True)
class CodexGatewayResponse:
    """HTTP response payload produced by the gateway runtime."""

    status_code: int
    body: dict[str, Any]


class CodexGatewayRuntime:
    """Authenticate, authorize, dispatch, and audit OpenAI-compatible calls."""

    def __init__(
        self,
        *,
        repository: CodexGatewayRepository,
        upstream_client: CodexGatewayUpstreamClient | None = None,
        policy_engine: CodexGatewayPolicyEngine | None = None,
    ):
        self.repository = repository
        self.upstream_client = (
            upstream_client or UnconfiguredCodexGatewayUpstreamClient()
        )
        self.policy_engine = policy_engine or CodexGatewayPolicyEngine()

    async def create_response(
        self,
        *,
        gateway_key: str,
        payload: dict[str, Any],
        source_ip: str | None = None,
        client_user_agent: str | None = None,
        client_type: str = "openai-compatible",
    ) -> CodexGatewayResponse:
        request_id = f"req_bifrost_{uuid4().hex}"
        model = payload.get("model")
        streaming = bool(payload.get("stream") or payload.get("streaming"))

        invalid_key_response = self._error(
            status_code=401,
            code="invalid_gateway_key",
            message="The Bifrost Codex Gateway key is invalid or revoked.",
        )
        if not is_plausible_gateway_key(gateway_key):
            return invalid_key_response

        key_record = await self.repository.get_active_gateway_key_by_plaintext(
            gateway_key
        )
        if key_record is None:
            return invalid_key_response

        if not isinstance(model, str) or not model:
            response = self._error(
                status_code=400,
                code="missing_model",
                message="The request body must include a model.",
                param="model",
            )
            await self._log_denial(
                request_id=request_id,
                endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
                model=model if isinstance(model, str) else None,
                status_code=response.status_code,
                code="missing_model",
                source_ip=source_ip,
                client_user_agent=client_user_agent,
            )
            return response

        try:
            account_record = (
                await self.repository.get_active_upstream_account_for_user(
                    key_record.user_id
                )
            )
        except MultipleResultsFound:
            response = self._error(
                status_code=403,
                code="upstream_identity_ambiguous",
                message="Multiple active ChatGPT/Codex accounts are connected for this Bifrost user.",
            )
            await self._log_denial(
                request_id=request_id,
                endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
                model=model,
                status_code=response.status_code,
                code="upstream_identity_ambiguous",
                key=key_record,
                source_ip=source_ip,
                client_user_agent=client_user_agent,
            )
            return response
        if account_record is None:
            response = self._error(
                status_code=403,
                code="upstream_identity_not_connected",
                message="No active ChatGPT/Codex account is connected for this Bifrost user.",
            )
            await self._log_denial(
                request_id=request_id,
                endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
                model=model,
                status_code=response.status_code,
                code="upstream_identity_not_connected",
                key=key_record,
                source_ip=source_ip,
                client_user_agent=client_user_agent,
            )
            return response

        key_context = self._key_context(key_record)
        account_context = self._account_context(account_record)
        request_context = CodexGatewayRequestContext(
            request_id=request_id,
            endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
            model=model,
            streaming=streaming,
            client_type=client_type,
            source_ip=source_ip,
            client_user_agent=client_user_agent,
        )
        decision = self.policy_engine.evaluate(
            key_context,
            account_context,
            request_context,
        )
        if not decision.allowed:
            response = CodexGatewayResponse(
                status_code=decision.status_code,
                body={"error": decision.openai_error.model_dump()},
            )
            await self.repository.create_request_log(
                request_id=request_id,
                user_id=key_record.user_id,
                project_id=key_record.project_id,
                gateway_key_id=key_record.id,
                oauth_account_id=account_record.id,
                endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
                model=model,
                streaming=streaming,
                status_code=decision.status_code,
                policy_decision="deny",
                denied_reason=decision.code,
                source_ip=source_ip,
                client_user_agent=client_user_agent,
                request_metadata=decision.audit_metadata,
            )
            return response

        if not account_record.encrypted_access_token:
            response = self._error(
                status_code=403,
                code="upstream_token_unavailable",
                message="The connected ChatGPT/Codex account does not have an active access token.",
            )
            await self._log_denial(
                request_id=request_id,
                endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
                model=model,
                status_code=response.status_code,
                code="upstream_token_unavailable",
                key=key_record,
                account=account_record,
                source_ip=source_ip,
                client_user_agent=client_user_agent,
            )
            return response

        try:
            access_token = decrypt_secret(account_record.encrypted_access_token)
        except Exception:
            response = self._error(
                status_code=403,
                code="upstream_token_unavailable",
                message="The connected ChatGPT/Codex account token is invalid or unavailable.",
            )
            await self._log_denial(
                request_id=request_id,
                endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
                model=model,
                status_code=response.status_code,
                code="upstream_token_unavailable",
                key=key_record,
                account=account_record,
                source_ip=source_ip,
                client_user_agent=client_user_agent,
            )
            return response

        start = perf_counter()
        try:
            upstream_response = await wait_for(
                self.upstream_client.create_response(
                    access_token=access_token,
                    payload=payload,
                ),
                timeout=CODEX_GATEWAY_UPSTREAM_TIMEOUT_SECONDS,
            )
        except AsyncioTimeoutError:
            upstream_response = self._upstream_error(
                status_code=504,
                code="upstream_timeout",
                message="The upstream ChatGPT/Codex request timed out.",
            )
        except Exception:
            upstream_response = self._upstream_error(
                status_code=502,
                code="upstream_unavailable",
                message="The upstream ChatGPT/Codex transport failed.",
            )
        latency_ms = int((perf_counter() - start) * 1000)

        await self.repository.create_request_log(
            request_id=request_id,
            user_id=key_record.user_id,
            project_id=key_record.project_id,
            gateway_key_id=key_record.id,
            oauth_account_id=account_record.id,
            endpoint=CODEX_GATEWAY_RESPONSES_ENDPOINT,
            model=model,
            streaming=streaming,
            status_code=upstream_response.status_code,
            provider_error_code=upstream_response.provider_error_code,
            input_token_count=upstream_response.input_token_count,
            output_token_count=upstream_response.output_token_count,
            latency_ms=latency_ms,
            policy_decision="allow",
            source_ip=source_ip,
            client_user_agent=client_user_agent,
            request_metadata=decision.audit_metadata,
        )
        return CodexGatewayResponse(
            status_code=upstream_response.status_code,
            body=upstream_response.body,
        )

    def _upstream_error(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
    ) -> CodexGatewayUpstreamResponse:
        return CodexGatewayUpstreamResponse(
            status_code=status_code,
            provider_error_code=code,
            body={
                "error": {
                    "message": message,
                    "type": "server_error",
                    "param": None,
                    "code": code,
                }
            },
        )

    def _error(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        param: str | None = None,
    ) -> CodexGatewayResponse:
        return CodexGatewayResponse(
            status_code=status_code,
            body={
                "error": OpenAICompatibleError(
                    message=message,
                    code=code,
                    param=param,
                ).model_dump()
            },
        )

    async def _log_denial(
        self,
        *,
        request_id: str,
        endpoint: str,
        model: str | None,
        status_code: int,
        code: str,
        key: CodexGatewayKey | None = None,
        account: CodexGatewayUpstreamAccount | None = None,
        source_ip: str | None = None,
        client_user_agent: str | None = None,
    ) -> None:
        await self.repository.create_request_log(
            request_id=request_id,
            user_id=key.user_id if key else None,
            project_id=key.project_id if key else None,
            gateway_key_id=key.id if key else None,
            oauth_account_id=account.id if account else None,
            endpoint=endpoint,
            model=model,
            streaming=False,
            status_code=status_code,
            policy_decision="deny",
            denied_reason=code,
            source_ip=source_ip,
            client_user_agent=client_user_agent,
            request_metadata={
                "request_id": request_id,
                "user_id": str(key.user_id) if key else None,
                "gateway_key_id": str(key.id) if key else None,
                "project_id": str(key.project_id) if key and key.project_id else None,
                "oauth_account_id": str(account.id) if account else None,
                "endpoint": endpoint,
                "model": model,
                "policy_decision": "deny",
                "denied_reason": code,
            },
        )

    @staticmethod
    def _key_context(key: CodexGatewayKey) -> CodexGatewayKeyContext:
        return CodexGatewayKeyContext(
            id=key.id,
            user_id=key.user_id,
            project_id=key.project_id,
            name=key.name,
            allowed_models=key.allowed_models or [],
            denied_models=key.denied_models or [],
            daily_limit=key.daily_limit,
            monthly_limit=key.monthly_limit,
            status=cast(CodexGatewayKeyStatus, key.status),
        )

    @staticmethod
    def _account_context(
        account: CodexGatewayUpstreamAccount,
    ) -> CodexGatewayUpstreamAccountContext:
        return CodexGatewayUpstreamAccountContext(
            id=account.id,
            user_id=account.user_id,
            upstream_subject=account.upstream_subject,
            upstream_email=account.upstream_email,
            upstream_workspace_id=account.upstream_workspace_id,
            access_token_expires_at=account.access_token_expires_at,
            last_refresh_at=account.last_refresh_at,
            last_used_at=account.last_used_at,
            revoked_at=account.revoked_at,
        )


def extract_gateway_key(
    authorization: str | None,
    x_bifrost_codex_key: str | None,
) -> str | None:
    """Extract downstream key from OpenAI-compatible Bearer auth or fallback header."""
    if authorization and authorization[:7].lower() == "bearer ":
        token = authorization[7:].strip()
        return token or None
    return x_bifrost_codex_key
