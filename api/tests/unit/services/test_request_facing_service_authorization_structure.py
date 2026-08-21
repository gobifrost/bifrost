"""Prevent request-facing services from reintroducing legacy human gates."""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass
from pathlib import Path


SERVICES = Path(__file__).parents[3] / "src" / "services"


@dataclass(frozen=True, slots=True)
class LegacyServiceReadException:
    snippets: tuple[str, ...]
    category: str
    reason: str


def _exception(
    category: str,
    reason: str,
    *snippets: str,
) -> LegacyServiceReadException:
    return LegacyServiceReadException(
        snippets=snippets,
        category=category,
        reason=reason,
    )


DIRECT_SERVICE_PRIVILEGE_READ_EXCEPTIONS = {
    "agent_executor.py": (
        _exception(
            "runtime_context_materialization",
            "agent execution payloads still carry stable platform-admin compatibility flags",
            '"is_platform_admin": user.is_superuser,',
            "is_platform_admin=user.is_superuser if user else False,",
            "user.is_superuser if user else False,",
        ),
    ),
    "execution/async_executor.py": (
        _exception(
            "engine_runtime_bridge",
            "workflow execution context still serializes stable platform-admin flags",
            "is_platform_admin=context.is_platform_admin,",
        ),
    ),
    "execution/engine.py": (
        _exception(
            "engine_runtime_bridge",
            "workflow engine request model still carries the execution platform-admin flag",
            "is_platform_admin=request.is_platform_admin,",
        ),
    ),
    "builder/mcp_harness.py": (
        _exception(
            "builder_runtime_bridge",
            "Builder MCP harness serializes the already-authorized principal into MCP context",
            "is_platform_admin=principal.is_platform_admin,",
        ),
    ),
    "builder/runtime_authorization.py": (
        _exception(
            "builder_runtime_principal_materialization",
            "Builder runtime reloads public identity/role claims before resolving AuthorizationContext",
            "role_ids, role_names = await get_user_roles(user.id, db)",
            "is_superuser=user.is_superuser,",
        ),
    ),
    "mcp_server/auth.py": (
        _exception(
            "mcp_token_materialization",
            "MCP auth token validation and refresh preserve public is_superuser claims",
            '"is_superuser": user.is_superuser,',
        ),
        _exception(
            "mcp_role_claim_materialization",
            "MCP auth hydrates public role names for token context",
            "role_names = await self._get_user_roles(payload.get(\"sub\"))",
            "async def _get_user_roles(self, user_id: str | None) -> list[str]:",
        ),
    ),
    "mcp_server/gateway.py": (
        _exception(
            "mcp_transport_authorization",
            "MCP gateway has its own typed context and execution-history authorization is deferred with the execution-token phase",
            "elif self.context.is_platform_admin:",
            "if not self.context.is_platform_admin and execution.executed_by != UUID(",
            "if not self.context.is_platform_admin and agent_run.caller_user_id != str(",
            "if not self.context.is_platform_admin and pending.get(\"user_id\") != str(",
            "not self.context.is_platform_admin",
            "is_platform_admin=self.context.is_platform_admin,",
        ),
    ),
    "mcp_server/server.py": (
        _exception(
            "mcp_transport_materialization",
            "MCP server serializes the already-authenticated MCP context into downstream compatibility fields",
            "is_platform_admin=context.is_platform_admin,",
            "bypass_resource_roles=context.is_platform_admin,",
        ),
    ),
    "mcp_server/tools/_http_bridge.py": (
        _exception(
            "mcp_transport_materialization",
            "MCP HTTP bridge forwards the stable superuser claim for REST compatibility",
            '"is_superuser": bool(context.is_platform_admin),',
        ),
    ),
    "solution_scope.py": (
        _exception(
            "execution_runtime_bridge",
            "solution-scoped table resolution runs under ExecutionContext; delegated execution-token RBAC is deferred",
            "if not ctx.user.is_superuser:",
            "bypass_resource_admission=ctx.user.is_superuser,",
        ),
    ),
    "solutions/app_runtime_access.py": (
        _exception(
            "app_runtime_principal_materialization",
            "isolated app runtime reloads public identity/role claims before resolving app access",
            "role_ids, role_names = await get_user_roles(user.id, db)",
            "is_superuser=user.is_superuser,",
        ),
    ),
    "user_provisioning.py": (
        _exception(
            "role_compatibility_synchronization",
            "provisioning keeps built-in Role assignments and legacy public user flags synchronized",
            "enabled=user.is_superuser and not user.is_system,",
            "is_superuser=user.is_superuser,",
            "is_platform_admin=user.is_superuser,",
        ),
        _exception(
            "role_claim_materialization",
            "shared user role helper hydrates public/compatibility role IDs and names; boundary-local policy roles use AuthorizationContext separately",
            "async def get_user_roles(",
        ),
    ),
}


def _comment_or_string_lines(text: str) -> set[int]:
    ignored: set[int] = set()
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    for token in tokens:
        if token.type not in {tokenize.COMMENT, tokenize.STRING}:
            continue
        start_line = token.start[0]
        end_line = token.end[0]
        ignored.update(range(start_line, end_line + 1))
    return ignored


def test_service_legacy_privilege_read_exceptions_have_reasons() -> None:
    offenders: list[str] = []
    for file_name, entries in DIRECT_SERVICE_PRIVILEGE_READ_EXCEPTIONS.items():
        for entry in entries:
            if not entry.category.strip():
                offenders.append(f"{file_name}: missing category")
            if entry.category == "human_authorization_debt":
                offenders.append(f"{file_name}: human authorization debt is not allowed")
            if not entry.reason.strip():
                offenders.append(f"{file_name}: missing reason")
            if not entry.snippets:
                offenders.append(f"{file_name}: missing snippets")

    assert offenders == []


def test_request_facing_service_legacy_privilege_reads_are_classified() -> None:
    offenders: list[str] = []
    needles = (
        ".is_superuser",
        ".is_platform_admin",
        ".is_provider_org",
        "has_platform_admin_grant(",
        'getattr(user, "is_superuser"',
        "getattr(user, 'is_superuser'",
        "get_user_roles(",
    )
    for path in sorted(SERVICES.rglob("*.py")):
        relative = path.relative_to(SERVICES)
        relative_key = relative.as_posix()
        allowed = tuple(
            snippet
            for entry in DIRECT_SERVICE_PRIVILEGE_READ_EXCEPTIONS.get(relative_key, ())
            for snippet in entry.snippets
        )
        text = path.read_text()
        ignored_lines = _comment_or_string_lines(text)
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line_number in ignored_lines:
                continue
            stripped = line.strip()
            if not any(needle in stripped for needle in needles):
                continue
            if any(snippet in stripped for snippet in allowed):
                continue
            offenders.append(f"{relative}:{line_number}: {stripped}")

    assert offenders == [], (
        "Request-facing services must not use legacy identity/admin booleans as "
        "human authorization gates. Use AuthorizationContext for converted "
        "paths, delete dead helpers, or add an exact exception with a "
        "compatibility/deferred-runtime reason: "
        + "; ".join(offenders)
    )
