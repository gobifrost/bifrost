"""Prevent new human routes from returning to legacy administrator gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROUTERS = Path(__file__).parents[3] / "src" / "routers"

# These are typed non-human/deferred execution surfaces, not precedents for
# human platform routes. Execution-token RBAC is explicitly deferred; SDK
# module fetches authenticate pre-minted engine tokens inside worker children.
LEGACY_GATE_EXCEPTIONS = {
    "executions.py": "workflow execution authorization is a separately deferred migration",
    "sdk_modules.py": "engine-token-only worker child transport",
}

@dataclass(frozen=True, slots=True)
class LegacyPrivilegeReadException:
    snippets: tuple[str, ...]
    category: str
    reason: str


def _exception(
    category: str,
    reason: str,
    *snippets: str,
) -> LegacyPrivilegeReadException:
    return LegacyPrivilegeReadException(
        snippets=snippets,
        category=category,
        reason=reason,
    )


# Direct reads of legacy identity/admin booleans are allowed only when they are
# deliberately materializing compatibility contracts, protecting deferred
# execution/embed transports, or mapping an already-authorized request into an
# older repository/runtime API. New human route authorization must go through
# CurrentAuthorizationContext instead.
DIRECT_PRIVILEGE_READ_EXCEPTIONS = {
    "auth.py": (
        _exception(
            "compatibility_materialization",
            "JWT/profile/login responses intentionally retain public is_superuser fields",
            '"is_superuser": user.is_superuser,',
            '"is_superuser": existing_user.is_superuser,',
            "is_superuser=existing_user.is_superuser,",
            "is_superuser=current_user.is_superuser,",
            "is_superuser=user.is_superuser,",
            'details={"email": user.email, "is_superuser": user.is_superuser},',
        ),
    ),
    "cli.py": (
        _exception(
            "compatibility_materialization",
            "CLI developer context responses retain stable public is_superuser fields",
            '"is_superuser": current_user.is_superuser,',
            "is_platform_admin=current_user.is_platform_admin,",
        ),
        _exception(
            "deferred_execution_rbac",
            "SDK execution scope resolver still preserves established engine/CLI scope behavior until the workflow execution-token phase",
            "is_platform_admin = current_user.is_superuser",
            "if needs_bypass_check and not is_platform_admin and caller_org_id is not None:",
            "is_provider_org = bool(org_row.scalar_one_or_none())",
            "is_platform_admin=is_platform_admin,",
            "is_provider_org=is_provider_org,",
        ),
    ),
    "executions.py": (
        _exception(
            "deferred_execution_rbac",
            "workflow execution-token RBAC and sensitive execution detail gating are deferred",
            "if not user.is_superuser:",
            "if not user.is_superuser and execution.executed_by != user.user_id:",
            'hidden_levels = set() if user.is_superuser else {"DEBUG", "TRACEBACK"}',
            "variables=execution.variables if user.is_superuser else None,",
            "execution_context=execution.execution_context if user.is_superuser else None,",
            "peak_memory_bytes=execution.peak_memory_bytes if user.is_superuser else None,",
            "process_rss_bytes=execution.process_rss_bytes if user.is_superuser else None,",
            "cpu_total_seconds=execution.cpu_total_seconds if user.is_superuser else None,",
            "if not user.is_superuser and row.executed_by != user.user_id:",
            "is_admin = user.is_superuser if user else False",
        ),
    ),
    "endpoints.py": (
        _exception(
            "runtime_bridge",
            "workflow endpoint execution context still carries the stable platform-admin flag",
            "is_platform_admin=context.is_platform_admin,",
        ),
    ),
    "files.py": (
        _exception(
            "target_materialization",
            "file target helper materializes the selected target into an existing repository flag",
            "is_superuser=target.is_superuser,",
        ),
    ),
    "forms.py": (
        _exception(
            "embed_runtime_bridge",
            "form launch/embed/execution paths still serialize platform-admin compatibility flags",
            "is_platform_admin=ctx.user.is_superuser,",
        ),
    ),
    "mcp.py": (
        _exception(
            "compatibility_transport",
            "MCP token and schema surfaces retain stable admin fields for external clients",
            '"is_platform_admin": current_user.is_superuser,',
            "is_superuser=current_user.is_superuser,",
        ),
    ),
    "mfa.py": (
        _exception(
            "compatibility_materialization",
            "MFA token refresh preserves the public is_superuser claim",
            '"is_superuser": user.is_superuser,',
        ),
    ),
    "oauth_sso.py": (
        _exception(
            "compatibility_materialization",
            "SSO token minting preserves the public is_superuser claim",
            '"is_superuser": user.is_superuser,',
        ),
    ),
    "profile.py": (
        _exception(
            "compatibility_materialization",
            "profile responses intentionally retain public is_superuser fields",
            "is_superuser=user.is_superuser,",
        ),
    ),
    "roles.py": (
        _exception(
            "role_compatibility_materialization",
            "role assignment keeps the legacy user column in sync for public compatibility",
            "target_user.is_superuser = True",
            "target_user.is_superuser = False",
        ),
    ),
    "sandbox_jobs.py": (
        _exception(
            "runtime_bridge",
            "sandbox runner callback payload still includes the stable platform-admin flag",
            '"is_platform_admin": user.is_superuser,',
        ),
    ),
    "solution_builder.py": (
        _exception(
            "target_materialization",
            "Builder target response exposes compatibility status from canonical target resolution",
            "is_platform_admin=targets.is_platform_admin,",
        ),
    ),
    "users.py": (
        _exception(
            "role_compatibility_materialization",
            "user admin routes map Platform Admin role state to the legacy public is_superuser column",
            "query = query.where(UserORM.is_superuser.is_(True))",
            "query = query.where(UserORM.is_superuser.is_(False))",
            "if request.is_superuser:",
            "is_superuser=request.is_superuser,",
            "if u.is_superuser and target is not None and target != PROVIDER_ORG_ID:",
            "is_superuser=u.is_superuser,",
            "u.is_superuser = PLATFORM_ADMIN_ROLE_ID in selected_role_ids",
            "if request.is_superuser is not None:",
            "is_superuser=new_user.is_superuser,",
            '"is_superuser": new_user.is_superuser,',
            "db_user.is_superuser = True",
            "elif db_user.is_superuser:",
            "db_user.is_superuser = False",
            "if not db_user.is_superuser:",
            "is_superuser=db_user.is_superuser,",
            "if db_user.is_superuser:",
            "and request.is_superuser is not True",
        ),
    ),
    "workflows.py": (
        _exception(
            "deferred_execution_rbac",
            "workflow execution authority and cancellation remain in the deferred execution-token phase",
            "exec_is_admin = run_as_user.is_superuser",
            "not ctx.user.is_superuser",
            "if not ctx.user.is_superuser and row.executed_by != ctx.user.user_id:",
        ),
    ),
}


def test_legacy_admin_dependencies_exist_only_on_classified_exceptions() -> None:
    offenders: list[str] = []
    needles = ("CurrentSuperuser", "get_current_superuser", "RequirePlatformAdmin")

    for path in sorted(ROUTERS.rglob("*.py")):
        text = path.read_text()
        if not any(needle in text for needle in needles):
            continue
        if path.name not in LEGACY_GATE_EXCEPTIONS:
            offenders.append(str(path.relative_to(ROUTERS)))

    assert offenders == [], (
        "Human routers must use CurrentAuthorizationContext; classify a truly "
        "non-human/deferred exception explicitly instead of adding a legacy gate: "
        + ", ".join(offenders)
    )


def test_classified_legacy_gate_exceptions_remain_narrow_and_documented() -> None:
    assert set(LEGACY_GATE_EXCEPTIONS) == {"executions.py", "sdk_modules.py"}
    assert all(reason.strip() for reason in LEGACY_GATE_EXCEPTIONS.values())


def test_direct_legacy_privilege_read_exceptions_have_reasons() -> None:
    offenders: list[str] = []
    for file_name, entries in DIRECT_PRIVILEGE_READ_EXCEPTIONS.items():
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


def test_direct_legacy_privilege_reads_are_classified() -> None:
    offenders: list[str] = []
    needles = (
        ".is_superuser",
        ".is_platform_admin",
        ".is_provider_org",
        "has_platform_admin_grant(",
        'getattr(user, "is_superuser"',
        "getattr(user, 'is_superuser'",
    )

    for path in sorted(ROUTERS.rglob("*.py")):
        allowed = tuple(
            snippet
            for entry in DIRECT_PRIVILEGE_READ_EXCEPTIONS.get(path.name, ())
            for snippet in entry.snippets
        )
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if not any(needle in stripped for needle in needles):
                continue
            if any(snippet in stripped for snippet in allowed):
                continue
            offenders.append(f"{path.relative_to(ROUTERS)}:{line_number}: {stripped}")

    assert offenders == [], (
        "Direct legacy privilege reads in human routers must be classified and "
        "kept out of new authorization decisions. Use CurrentAuthorizationContext "
        "for converted paths, or add a narrow exception with the compatibility/"
        "deferred-runtime reason: "
        + "; ".join(offenders)
    )
