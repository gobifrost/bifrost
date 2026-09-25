"""Shared business service for SDK ``ai`` operations.

Single implementation used by both entry points:

- the HTTP handlers (``api/src/routers/cli.py::cli_ai_complete`` and
  ``cli_ai_info``) serving external SDK/CLI callers, and
- the engine-local dispatcher
  (``api/src/services/execution/sdk_local_dispatch.py``) serving workflow
  and ``@service`` children through the parent-side local transport.

Both paths share completion input handling (DTO-shaped messages,
base64 input-file decoding, user-message requirement), profile/model/max
token selection, the provider fallback chain, response shaping, error
mapping, and best-effort usage attribution — so HTTP and local results
are identical by construction.

Only ``complete`` and ``get_model_info`` live here. ``ai.stream`` stays
on its existing handler until a later stage.

Parent-side only: imports SQLAlchemy sessions, the LLM factory, and the
usage service. A workflow child never imports this module (it stays
DB-free behind the dedicated local channel).
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.core.principal import UserPrincipal

logger = logging.getLogger(__name__)


class SdkAIError(Exception):
    """SDK AI failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the local dispatcher (``ok: false`` frames) can map the same
    failure to their own transport. Statuses preserve the historical
    handler mapping: 503 for configuration/value failures, 401 for
    provider authentication errors, 404 for a missing model config on
    ``get_model_info``, and 500 (sanitized detail) for anything else.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _provider_auth_error(exc: Exception) -> str | None:
    """Provider name when ``exc`` is a provider authentication failure."""
    error_type = type(exc).__name__
    error_module = type(exc).__module__
    if error_type == "AuthenticationError" and error_module in (
        "anthropic",
        "openai",
    ):
        return "Anthropic" if error_module == "anthropic" else "OpenAI"
    return None


async def complete_sdk_ai(
    session: AsyncSession,
    principal: UserPrincipal,
    *,
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
    model: str | None = None,
    profile: str | None = None,
    execution_id: str | None = None,
    scope: str | None = None,
    input_files: list[Any] | None = None,
) -> dict[str, Any]:
    """Run one non-streaming AI completion (shared by both transports).

    Args:
        session: Short-lived parent/HTTP database session. The DB
            connection is released after the profile lookup and before
            awaiting the external provider; usage recording reacquires
            the same session afterward.
        principal: The auth-verified trusted principal (HTTP caller or
            parent-derived dispatch identity). Never a child actor claim.
            Supplies the usage ``user_id`` and the scope-resolution
            caller org/admin gate.
        messages: DTO-shaped ``[{"role": ..., "content": ...}]`` list.
        max_tokens: Per-request output override (None = profile default).
        model: Per-request model override (None = profile model).
        profile: Model profile name (None = platform default profile).
        execution_id: Raw execution id string for usage attribution
            (malformed values fail best-effort, never fatal).
        scope: Raw scope string for usage attribution (None/"" = UNSET,
            "global" = global, UUID = org). Grammar/authorization
            failures are best-effort (warning) — never fatal.
        input_files: DTO-shaped file inputs (objects with ``filename``,
            ``content_type``, ``data_base64`` or equivalent dicts).

    Returns:
        JSON-serializable dict with ``content``, ``input_tokens``,
        ``output_tokens``, and ``model`` — the exact fields of
        ``CLIAICompleteResponse``.

    Raises:
        SdkAIError: 503 for configuration/value failures (including the
            file-inputs user-message requirement and base64 decode
            failures), 401 for provider authentication errors, 500
            (sanitized) for anything else.
    """
    from src.services.llm import LLMInputFile, LLMMessage, get_llm_client

    try:
        llm_messages = [
            LLMMessage(role=msg["role"], content=msg["content"])  # type: ignore[arg-type]
            for msg in messages
        ]
    except (KeyError, TypeError) as e:
        logger.error(f"CLI AI complete failed: {log_safe(e)}")
        raise SdkAIError(
            500, "AI completion failed. See server logs for details."
        ) from None

    if input_files:
        user_message = next(
            (
                message
                for message in reversed(llm_messages)
                if message.role == "user"
            ),
            None,
        )
        if user_message is None:
            raise SdkAIError(503, "AI file inputs require a user message.")
        try:
            decoded = []
            for item in input_files:
                if isinstance(item, dict):
                    filename = item["filename"]
                    media_type = item["content_type"]
                    data_base64 = item["data_base64"]
                else:
                    filename = item.filename
                    media_type = item.content_type
                    data_base64 = item.data_base64
                decoded.append(
                    LLMInputFile(
                        filename=filename,
                        media_type=media_type,
                        data=base64.b64decode(data_base64, validate=True),
                    )
                )
            user_message.input_files = decoded
        except SdkAIError:
            raise
        except ValueError as e:
            raise SdkAIError(503, str(e)) from None

    try:
        client = await get_llm_client(session, profile_name=profile)
    except ValueError as e:
        raise SdkAIError(503, str(e)) from None

    # Release the checked-out DB connection before provider latency. The
    # session stays usable: usage attribution below reacquires a
    # connection on the same session, and the HTTP ``get_db`` dependency
    # still owns the final commit/rollback.
    await session.close()

    try:
        response = await client.complete(
            messages=llm_messages,
            max_tokens=max_tokens,
            model=model,
        )
    except ValueError as e:
        raise SdkAIError(503, str(e)) from None
    except Exception as e:
        provider = _provider_auth_error(e)
        if provider is not None:
            logger.error(
                f"CLI AI complete failed: {provider} authentication error - invalid API key"
            )
            raise SdkAIError(
                401,
                f"{provider} API key is invalid or expired. Please update the API key in System Settings > AI Configuration.",
            ) from None
        logger.error(f"CLI AI complete failed: {log_safe(e)}")
        raise SdkAIError(
            500, "AI completion failed. See server logs for details."
        ) from None

    logger.info(
        f"CLI AI complete: model={log_safe(response.model)}, tokens={response.input_tokens}/{response.output_tokens}"
    )

    # Best-effort usage attribution: scope grammar/authorization failures,
    # redis failures, and malformed execution ids never fail the request.
    try:
        from src.core.cache import get_shared_redis
        from src.services.ai_usage_service import record_ai_usage
        from shared.sdk_config import (
            ScopeResolutionError,
            resolve_sdk_scope,
        )

        redis_client = await get_shared_redis()
        try:
            org_uuid = await resolve_sdk_scope(
                scope,
                caller_org_id=principal.organization_id,
                is_platform_admin=principal.is_superuser,
                session=session,
            )
        except ScopeResolutionError as e:
            # Scope grammar/authorization failures are best-effort here:
            # usage attribution is skipped, the completion still succeeds.
            logger.warning(f"Failed to record AI usage: {log_safe(e)}")
        else:
            await record_ai_usage(
                session=session,
                redis_client=redis_client,
                provider=client.provider_name,
                model=response.model or client.model_name,
                input_tokens=response.input_tokens or 0,
                output_tokens=response.output_tokens or 0,
                cache_read_tokens=response.cache_read_tokens,
                cache_write_tokens=response.cache_write_tokens,
                provider_cost=response.provider_cost,
                execution_id=UUID(execution_id) if execution_id else None,
                organization_id=org_uuid,
                user_id=principal.user_id,
            )
    except Exception as e:
        logger.warning(f"Failed to record AI usage: {log_safe(e)}")

    return {
        "content": response.content,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "model": response.model,
    }


async def get_sdk_model_info(
    session: AsyncSession,
    principal: UserPrincipal,  # noqa: ARG001 - trusted-caller gate for the future local path
) -> dict[str, Any]:
    """Return the configured provider/model (shared by both transports).

    Args:
        session: Short-lived parent/HTTP database session (short read).
        principal: The auth-verified trusted principal. Unused beyond
            the gate — the model config is platform-wide — but required
            so the future local dispatcher passes the same parent-derived
            identity it passes to :func:`complete_sdk_ai`.

    Returns:
        JSON-serializable dict with ``provider`` and ``model`` — the
        exact fields of ``CLIAIInfoResponse``.

    Raises:
        SdkAIError: 404 when no model configuration is present.
    """
    from src.services.llm.factory import get_llm_config

    try:
        config = await get_llm_config(session)
    except ValueError as e:
        raise SdkAIError(404, str(e)) from None

    return {"provider": config.provider, "model": config.model}
