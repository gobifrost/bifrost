"""Legacy-format runtime tokens for tests of the bridge (token PR1c deletes this).

Until token PR1c, routes that do not speak the claims protocol still receive
the pre-claims runtime token, and the API validates it from the database on
every request. Tests that need "a signed runtime token for this run" without
building every authority fact the claims minter reads use these.
"""

from __future__ import annotations

from typing import Any

from src.services.authorization.tokens import (
    LEGACY_SERVICE_TOKEN_SECONDS,
    RuntimeSubject,
    _legacy_runtime_token,
    legacy_engine_token_lifetime,
)


def legacy_engine_token(*, execution_id: Any, timeout_seconds: int) -> tuple[str, str]:
    """A legacy workflow-execution token and its ISO-8601 expiry."""
    token, expires_at = _legacy_runtime_token(
        RuntimeSubject("workflow_execution", execution_id),
        lifetime_seconds=legacy_engine_token_lifetime(timeout_seconds),
    )
    return token, expires_at.isoformat()


def legacy_service_token(
    *, service_id: Any, attempt_id: Any, lifetime_seconds: int = LEGACY_SERVICE_TOKEN_SECONDS
) -> tuple[str, str]:
    """A legacy service-attempt token and its ISO-8601 expiry."""
    token, expires_at = _legacy_runtime_token(
        RuntimeSubject("service_attempt", attempt_id, service_id), lifetime_seconds=lifetime_seconds
    )
    return token, expires_at.isoformat()
