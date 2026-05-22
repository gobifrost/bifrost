"""
Policy and identity resolution for the Bifrost Codex Gateway.

The gateway's core invariant is same-user attribution:
the Bifrost user represented by a downstream gateway key must be the same
Bifrost user whose upstream ChatGPT/Codex account is selected. This module is
pure business logic so routers and future app/admin surfaces can share it.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.models.contracts.codex_gateway import (
    CodexGatewayKeyContext,
    CodexGatewayPolicyDecision,
    CodexGatewayRequestContext,
    CodexGatewayUpstreamAccount,
    OpenAICompatibleError,
)


class CodexGatewayPolicyError(Exception):
    """Structured policy failure that can be mapped to OpenAI-style errors."""

    def __init__(self, code: str, message: str, *, status_code: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class CodexGatewayPolicyEngine:
    """Evaluate Bifrost-native gateway policy before upstream dispatch."""

    def resolve_upstream_account(
        self,
        key: CodexGatewayKeyContext,
        accounts: Iterable[CodexGatewayUpstreamAccount],
    ) -> CodexGatewayUpstreamAccount:
        """
        Select the active upstream account for the downstream key's user.

        Cross-user fallback is deliberately absent. If the matching account is
        missing, revoked, or ambiguous, callers must fail closed.
        """
        if key.status != "active":
            raise CodexGatewayPolicyError(
                "gateway_key_revoked",
                "The Bifrost Codex Gateway key is not active.",
                status_code=401,
            )

        matching = [account for account in accounts if account.user_id == key.user_id]
        if not matching:
            raise CodexGatewayPolicyError(
                "upstream_identity_not_connected",
                "No active ChatGPT/Codex account is connected for this Bifrost user.",
            )

        active = [account for account in matching if account.revoked_at is None]
        if not active:
            raise CodexGatewayPolicyError(
                "upstream_identity_revoked",
                "The connected ChatGPT/Codex account has been revoked.",
            )

        if len(active) > 1:
            raise CodexGatewayPolicyError(
                "upstream_identity_ambiguous",
                "Multiple active ChatGPT/Codex accounts are connected for this Bifrost user.",
            )

        return active[0]

    def evaluate(
        self,
        key: CodexGatewayKeyContext,
        account: CodexGatewayUpstreamAccount,
        request: CodexGatewayRequestContext,
    ) -> CodexGatewayPolicyDecision:
        """Return an allow/deny decision with metadata safe for audit logs."""
        if account.user_id != key.user_id:
            return self._deny(
                key,
                account,
                request,
                code="upstream_identity_mismatch",
                message="The downstream gateway identity does not match the upstream ChatGPT/Codex identity.",
            )

        if key.status != "active":
            return self._deny(
                key,
                account,
                request,
                code="gateway_key_revoked",
                message="The Bifrost Codex Gateway key is not active.",
                status_code=401,
            )

        if account.revoked_at is not None:
            return self._deny(
                key,
                account,
                request,
                code="upstream_identity_revoked",
                message="The connected ChatGPT/Codex account has been revoked.",
            )

        if request.model in key.denied_models:
            return self._deny(
                key,
                account,
                request,
                code="model_denied",
                message=f"Model '{request.model}' is denied by Bifrost Codex Gateway policy.",
                param="model",
            )

        if key.allowed_models and request.model not in key.allowed_models:
            return self._deny(
                key,
                account,
                request,
                code="model_not_allowed",
                message=f"Model '{request.model}' is not allowed by Bifrost Codex Gateway policy.",
                param="model",
            )

        return CodexGatewayPolicyDecision(
            allowed=True,
            code="allowed",
            message="Request allowed by Bifrost Codex Gateway policy.",
            status_code=200,
            audit_metadata=self._audit_metadata(key, account, request, "allow"),
            openai_error=OpenAICompatibleError(
                message="",
                code="allowed",
            ),
        )

    def filter_visible_models(
        self,
        key: CodexGatewayKeyContext,
        upstream_model_ids: Iterable[str],
    ) -> list[str]:
        """Apply downstream allow/deny policy to the upstream-visible model list."""
        allowed = set(key.allowed_models)
        denied = set(key.denied_models)
        visible: list[str] = []
        for model_id in upstream_model_ids:
            if model_id in denied:
                continue
            if allowed and model_id not in allowed:
                continue
            visible.append(model_id)
        return visible

    def _deny(
        self,
        key: CodexGatewayKeyContext,
        account: CodexGatewayUpstreamAccount,
        request: CodexGatewayRequestContext,
        *,
        code: str,
        message: str,
        status_code: int = 403,
        param: str | None = None,
    ) -> CodexGatewayPolicyDecision:
        return CodexGatewayPolicyDecision(
            allowed=False,
            code=code,
            message=message,
            status_code=status_code,
            audit_metadata=self._audit_metadata(key, account, request, "deny", code),
            openai_error=OpenAICompatibleError(
                message=message,
                code=code,
                param=param,
            ),
        )

    def _audit_metadata(
        self,
        key: CodexGatewayKeyContext,
        account: CodexGatewayUpstreamAccount,
        request: CodexGatewayRequestContext,
        policy_decision: str,
        denied_reason: str | None = None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "request_id": request.request_id,
            "user_id": str(key.user_id),
            "gateway_key_id": str(key.id),
            "project_id": str(key.project_id) if key.project_id else None,
            "oauth_account_id": str(account.id),
            "upstream_subject": account.upstream_subject,
            "upstream_email": account.upstream_email,
            "upstream_workspace_id": account.upstream_workspace_id,
            "endpoint": request.endpoint,
            "model": request.model,
            "streaming": request.streaming,
            "client_type": request.client_type,
            "source_ip": request.source_ip,
            "client_user_agent": request.client_user_agent,
            "input_token_count": request.input_token_count,
            "output_token_count": request.output_token_count,
            "policy_decision": policy_decision,
            "denied_reason": denied_reason,
        }

        if request.prompt_capture_enabled:
            metadata["sensitive_input_preview"] = request.sensitive_input_preview
        if request.response_capture_enabled:
            metadata["sensitive_output_preview"] = request.sensitive_output_preview

        return metadata
