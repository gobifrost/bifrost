"""Classify legacy privilege reads below the router/service layer.

Human request authorization should enter through ``AuthorizationContext``.
Repositories and shared helpers still contain a small number of legacy boolean
reads because they are lower-level bridge APIs, public claim materializers, or
deferred execution-visibility code. This test keeps that set explicit.
"""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass
from pathlib import Path


API_ROOT = Path(__file__).parents[2]
SCAN_ROOTS = (
    API_ROOT / "src" / "repositories",
    API_ROOT / "shared",
)


@dataclass(frozen=True, slots=True)
class LegacyReadException:
    snippets: tuple[str, ...]
    category: str
    reason: str


def _exception(
    category: str,
    reason: str,
    *snippets: str,
) -> LegacyReadException:
    return LegacyReadException(
        snippets=snippets,
        category=category,
        reason=reason,
    )


LEGACY_READ_EXCEPTIONS: dict[str, tuple[LegacyReadException, ...]] = {
    "src/repositories/executions.py": (
        _exception(
            "deferred_execution_rbac",
            "execution visibility and sensitive execution detail gating remain in the later delegated execution-token phase",
            "if not user.is_superuser:",
            "if not user.is_superuser and execution.executed_by != user.user_id:",
            "variables=execution.variables if user.is_superuser else None,",
            "execution_context=execution.execution_context if user.is_superuser else None,",
            "peak_memory_bytes=execution.peak_memory_bytes if user.is_superuser else None,",
            "cpu_total_seconds=execution.cpu_total_seconds if user.is_superuser else None,",
            "if not user.is_superuser and row.executed_by != user.user_id:",
            "is_admin = user.is_superuser if user else False",
        ),
    ),
    "shared/execution_timeseries.py": (
        _exception(
            "deferred_execution_rbac",
            "execution timeseries ownership filtering remains coupled to deferred execution visibility semantics",
            "if not user.is_superuser:",
        ),
    ),
    "shared/external_access.py": (
        _exception(
            "compatibility_materialization",
            "token mint helpers preserve public is_external/is_provider_org compatibility claims",
            "if user.is_superuser:",
        ),
    ),
    "shared/form_provider.py": (
        _exception(
            "embed_runtime_bridge",
            "form launch serializes stable platform-admin compatibility fields for execution context",
            "is_platform_admin=user.is_superuser,",
        ),
    ),
    "shared/pending_execution.py": (
        _exception(
            "deferred_execution_rbac",
            "pending execution read-through visibility is part of the deferred execution-token phase",
            "if not user.is_superuser and pending_user_id != str(user.user_id):",
        ),
    ),
    "shared/scope_resolver.py": (
        _exception(
            "deferred_execution_rbac",
            "CLI/SDK execution scope resolver still preserves provider-org bypass until the workflow execution-token phase",
            "is_platform_admin: bool,",
            "is_provider_org: bool = False,",
        ),
    ),
}


def _comment_or_string_lines(text: str) -> set[int]:
    ignored: set[int] = set()
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    for token in tokens:
        if token.type not in {tokenize.COMMENT, tokenize.STRING}:
            continue
        ignored.update(range(token.start[0], token.end[0] + 1))
    return ignored


def test_repository_shared_legacy_privilege_read_exceptions_have_reasons() -> None:
    offenders: list[str] = []
    for relative_path, entries in LEGACY_READ_EXCEPTIONS.items():
        if not (API_ROOT / relative_path).exists():
            offenders.append(f"{relative_path}: missing file")
        for entry in entries:
            if entry.category == "human_authorization_debt":
                offenders.append(f"{relative_path}: human authorization debt is not allowed")
            if not entry.category.strip():
                offenders.append(f"{relative_path}: missing category")
            if not entry.reason.strip():
                offenders.append(f"{relative_path}: missing reason")
            if not entry.snippets:
                offenders.append(f"{relative_path}: missing snippets")

    assert offenders == []


def test_repository_shared_legacy_privilege_reads_are_classified() -> None:
    offenders: list[str] = []
    needles = (
        ".is_superuser",
        ".is_platform_admin",
        ".is_provider_org",
        "has_platform_admin_grant(",
        "is_platform_admin:",
        "is_provider_org:",
        "is_platform_admin =",
        "is_provider_org =",
    )

    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(API_ROOT).as_posix()
            allowed = tuple(
                snippet
                for entry in LEGACY_READ_EXCEPTIONS.get(relative, ())
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
        "Repositories/shared helpers must not add unclassified legacy "
        "privilege reads. Human request authorization belongs in "
        "AuthorizationContext; retained lower-level bridges must be exact "
        "exceptions with compatibility/deferred-runtime reasons: "
        + "; ".join(offenders)
    )
