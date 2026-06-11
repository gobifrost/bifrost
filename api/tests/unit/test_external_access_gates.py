"""
External-user isolation: gate tests + mechanical plumbing enforcement (EXT-1).

Three things live here:

1. ``resolve_external_claim`` — the token-mint helper that neutralizes the
   raw ``User.is_external`` flag for bypass principals
   (``is_platform_admin OR is_provider_org`` — the canonical C2 rule).
2. The authenticated-tier gates that live OUTSIDE ``OrgScopedRepository``:
   the agents-router tool-attach validation and the MCP agent access check.
3. A mechanical lint: every org-scoped repository construction that passes a
   principal-derived ``is_superuser`` must also pass ``is_external``.
   Forgetting the flag at a new call site silently re-grants the global
   tier to external users — this test makes that a red build instead.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.external_access import resolve_external_claim

API_SRC = Path(__file__).resolve().parents[2] / "src"


# =============================================================================
# 1. resolve_external_claim (token mint)
# =============================================================================


def _user(is_external=True, is_superuser=False, organization_id=...):
    u = MagicMock(spec=["is_external", "is_superuser", "organization_id"])
    u.is_external = is_external
    u.is_superuser = is_superuser
    u.organization_id = uuid4() if organization_id is ... else organization_id
    return u


class TestResolveExternalClaim:
    async def test_non_external_user_is_false(self):
        db = AsyncMock()
        assert await resolve_external_claim(db, _user(is_external=False)) is False
        db.scalar.assert_not_awaited()

    async def test_platform_admin_is_neutralized(self):
        db = AsyncMock()
        assert (
            await resolve_external_claim(db, _user(is_superuser=True)) is False
        )
        db.scalar.assert_not_awaited()

    async def test_provider_org_member_is_neutralized(self):
        db = AsyncMock()
        db.scalar.return_value = True  # org.is_provider
        assert await resolve_external_claim(db, _user()) is False

    async def test_regular_org_external_is_true(self):
        db = AsyncMock()
        db.scalar.return_value = False
        assert await resolve_external_claim(db, _user()) is True

    async def test_orgless_external_is_false(self):
        db = AsyncMock()
        assert (
            await resolve_external_claim(db, _user(organization_id=None)) is False
        )
        db.scalar.assert_not_awaited()


# =============================================================================
# 2a. Agents router: _validate_user_tool_access
# =============================================================================


def _rows_result(values):
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _workflow(access_level: str):
    wf = MagicMock()
    wf.is_active = True
    wf.access_level = access_level
    wf.name = "wf"
    return wf


class TestValidateUserToolAccessExternal:
    async def test_authenticated_workflow_denied_for_external_without_role(self):
        from src.routers.agents import _validate_user_tool_access

        tool_id = str(uuid4())
        db = AsyncMock()
        db.execute.side_effect = [
            _rows_result([]),  # user's roles: none
            _scalar_result(_workflow("authenticated")),  # the workflow
            _rows_result([]),  # workflow's roles: none
        ]
        with pytest.raises(HTTPException) as exc:
            await _validate_user_tool_access(
                db, uuid4(), [tool_id], is_external=True
            )
        assert exc.value.status_code == 403

    async def test_authenticated_workflow_allowed_for_regular_user(self):
        from src.routers.agents import _validate_user_tool_access

        tool_id = str(uuid4())
        db = AsyncMock()
        db.execute.side_effect = [
            _rows_result([]),  # user's roles: none
            _scalar_result(_workflow("authenticated")),
        ]
        await _validate_user_tool_access(db, uuid4(), [tool_id])

    async def test_role_based_workflow_allowed_for_external_with_role(self):
        from src.routers.agents import _validate_user_tool_access

        role_id = uuid4()
        tool_id = str(uuid4())
        db = AsyncMock()
        db.execute.side_effect = [
            _rows_result([role_id]),  # user's roles
            _scalar_result(_workflow("role_based")),
            _rows_result([role_id]),  # workflow's roles
        ]
        await _validate_user_tool_access(db, uuid4(), [tool_id], is_external=True)


# =============================================================================
# 2b. MCP: _check_agent_access
# =============================================================================


class TestMCPAgentAccessExternal:
    def _agent(self, access_level, role_names=()):
        from src.models.enums import AgentAccessLevel

        agent = MagicMock()
        agent.access_level = AgentAccessLevel(access_level)
        agent.roles = [MagicMock(name=n) for n in role_names]
        for role, n in zip(agent.roles, role_names):
            role.name = n
        return agent

    def _check(self, agent, user_roles, is_superuser=False, is_external=False):
        from src.services.mcp_server.tool_access import MCPToolAccessService

        return MCPToolAccessService._check_agent_access(
            agent, user_roles, is_superuser, is_external
        )

    def test_authenticated_agent_denied_for_external(self):
        agent = self._agent("authenticated")
        assert self._check(agent, [], is_external=True) is False

    def test_authenticated_agent_allowed_for_regular_user(self):
        agent = self._agent("authenticated")
        assert self._check(agent, []) is True

    def test_authenticated_agent_allowed_for_external_superuser(self):
        agent = self._agent("authenticated")
        assert self._check(agent, [], is_superuser=True, is_external=True) is True

    def test_role_based_agent_allowed_for_external_with_role(self):
        agent = self._agent("role_based", role_names=("Portal User",))
        assert self._check(agent, ["Portal User"], is_external=True) is True


# =============================================================================
# 3. Mechanical plumbing lint
# =============================================================================


def _org_scoped_repo_class_names() -> set[str]:
    """Collect OrgScopedRepository subclass names from src/repositories/."""
    names: set[str] = set()
    for path in (API_SRC / "repositories").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_src = ast.unparse(base)
                    if base_src.startswith("OrgScopedRepository"):
                        names.add(node.name)
    return names


# The OrgScopedRepository signature: positional args after ``session`` are
# ``org_id, user_id, is_superuser, is_external``. A positional 4th arg IS
# ``is_superuser``; the lint must inspect positionals too, not just kwargs.
_REPO_POSITIONAL_NAMES = ["session", "org_id", "user_id", "is_superuser", "is_external"]


def _is_external_value_present(node: ast.Call) -> bool:
    """True if the call supplies is_external (positionally or by keyword)."""
    if any(kw.arg == "is_external" for kw in node.keywords):
        return True
    idx = _REPO_POSITIONAL_NAMES.index("is_external")
    return len(node.args) > idx


def _is_superuser_value(node: ast.Call) -> ast.expr | None:
    """Return the is_superuser argument node (positional or kw), or None."""
    for kw in node.keywords:
        if kw.arg == "is_superuser":
            return kw.value
    idx = _REPO_POSITIONAL_NAMES.index("is_superuser")
    if len(node.args) > idx:
        return node.args[idx]
    return None


def test_principal_derived_repo_constructions_pass_is_external():
    """Every org-scoped repo construction with a principal-derived
    ``is_superuser`` must also pass ``is_external`` — whether the args are
    keyword OR positional.

    A literal ``is_superuser=True``/``False`` means a fixed identity (engine
    sentinel / forced-regular path) and is exempt. Anything else is a real
    principal whose external-ness must travel with it — otherwise an external
    user regains the global tier at that call site.
    """
    repo_names = _org_scoped_repo_class_names()
    assert repo_names, "failed to discover OrgScopedRepository subclasses"

    violations: list[str] = []
    for path in API_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute) else None
            )
            if name not in repo_names:
                continue
            su = _is_superuser_value(node)
            if su is None or isinstance(su, ast.Constant):
                continue  # no identity / fixed identity (sentinel) — exempt
            if not _is_external_value_present(node):
                violations.append(
                    f"{path.relative_to(API_SRC)}:{node.lineno} — {name}("
                    f"is_superuser={ast.unparse(su)}, ...) missing is_external"
                )

    assert not violations, (
        "Org-scoped repository constructed with a principal-derived "
        "is_superuser but no is_external. Pass the principal's "
        "is_external (e.g. is_external=ctx.user.is_external) so external "
        "users don't silently regain the global tier:\n  "
        + "\n  ".join(violations)
    )


# =============================================================================
# 3b. METHOD-level lint: repo methods must not hand-roll the global arm.
# =============================================================================

# Repository read-cascade methods (org-OR-global *read*) that resolve the
# global tier and must therefore honor ``self.external_restricted``. Each
# entry is ``"<file_stem>.<method>"`` with a one-line reason pointing at the
# guard / dedicated test. Keep SHORT — every entry is a method the structural
# lint trusts to gate externals itself.
# SELF-GUARDING read-cascade methods: they keep an inline global arm but
# branch on self.external_restricted/is_external themselves. The lint VERIFIES
# the guard is still present — if a future edit strips it (LEAK #4's failure
# mode), the method drops out of this set's protection and becomes a violation.
_METHOD_SELF_GUARDING: dict[str, str] = {
    "agents.list_agents": "branches on self.external_restricted (commit cb7f9181)",
    "agents.get_agent_with_access_check": "early-returns on self.external_restricted",
    "forms.get_form_with_access_check": "early-returns on self.external_restricted",
    "knowledge.search": "forces org-only when self.external_restricted",
    "knowledge.list_namespaces": "forces org-only when self.external_restricted",
    "knowledge.list_documents_by_namespace": "forces org-only when self.external_restricted",
    "workflows.list_tools_for_filter": "forces org-only when self.external_restricted",
    # merged_for_sdk is reachable behind the PLAIN CurrentUser endpoint
    # /api/cli/config/get — NOT sentinel-only. It honors an explicit `external`
    # flag (defaulting to self.is_external) that drops the global tier. EXT-1
    # NEW-1. Verified self-guarding: stripping the flag re-leaks global secrets.
    "config.merged_for_sdk": "drops global tier for external caller (EXT-1 NEW-1)",
}

# SENTINEL/ADMIN-ONLY methods: never reached by a direct external user, so the
# inline global arm is safe by CALL-SITE (verified manually). These are exempt
# without requiring an inline guard.
_METHOD_SENTINEL_ONLY: dict[str, str] = {
    "agents.list_all_in_scope": "superuser-only enumeration (OrgFilterType, router-gated)",
    "forms.list_all_in_scope": "superuser-only enumeration (OrgFilterType, router-gated)",
    "applications.list_all_in_scope": "superuser-only enumeration (OrgFilterType, router-gated)",
    "external_mcp.list_all_in_scope": "superuser-only enumeration (OrgFilterType, router-gated)",
    "tables.list_tables": "CurrentSuperuser route only (is_superuser=True caller)",
    "config.list_configs": "superuser-only config endpoint (is_superuser=True)",
    "oauth.get_token": "SDK/engine-or-superuser gated (is_superuser=True)",
    "oauth.get_org_level_for_provider": "SDK/engine token resolve (is_superuser=True)",
    "knowledge.get_by_key": "SDK/CLI sentinel knowledge read (is_superuser=True)",
    "knowledge.get_all_by_namespace": "docs-indexer sentinel read (is_superuser=True)",
}


def _method_does_org_or_global_read(method: ast.AST) -> bool:
    """True if the method builds an org-OR-global READ cascade — i.e. it pairs
    a global-arm ``organization_id.is_(None)`` with an org-arm
    ``organization_id == <x>`` inside a SELECT, the shape that leaks the global
    tier on reads. Pure-global writes/admin lookups (only the global arm, no
    paired org arm, or a delete/update) don't match.
    """
    src = ast.unparse(method)
    if "organization_id.is_(None)" not in src:
        return False
    has_org_arm = "organization_id ==" in src or "organization_id.in_(" in src
    is_write = "delete(" in src or ".update(" in src.lower()
    # An or_( ... is_(None) ) or a paired org-arm select is a read cascade.
    is_cascade_shape = "or_(" in src or has_org_arm
    return is_cascade_shape and not is_write


def test_repo_read_cascade_methods_honor_external_restricted():
    """A repository READ method that builds an org-OR-global cascade by hand
    (instead of ``_apply_cascade_scope``, which honors ``self.is_external``)
    re-opens the global tier to externals. This is the lint's previous blind
    spot — LEAK #4 lived in exactly such a method (``list_tools_for_filter``).

    Such a method must either route through ``_apply_cascade_scope`` or appear
    in ``_METHOD_CASCADE_EXTERNAL_ALLOWLIST`` with a reason (and branch on
    ``self.external_restricted`` itself, covered by a dedicated test).
    """
    repo_dir = API_SRC / "repositories"
    repo_names = _org_scoped_repo_class_names()

    violations: list[str] = []
    for path in repo_dir.glob("*.py"):
        stem = path.stem
        tree = ast.parse(path.read_text())
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef) or cls.name not in repo_names:
                continue
            for method in cls.body:
                if not isinstance(method, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    continue
                key = f"{stem}.{method.name}"
                # Sentinel/admin-only methods are exempt by call-site.
                if key in _METHOD_SENTINEL_ONLY:
                    continue
                if not _method_does_org_or_global_read(method):
                    continue
                src = ast.unparse(method)
                has_guard = "external_restricted" in src or "is_external" in src
                if key in _METHOD_SELF_GUARDING:
                    # VERIFY the self-guard is still present — a stripped guard
                    # (LEAK #4's failure mode) re-opens the global tier.
                    if not has_guard:
                        violations.append(
                            f"{path.relative_to(API_SRC)}::{cls.name}.{method.name} "
                            f"is allow-listed as self-guarding but no longer "
                            f"references self.external_restricted/is_external"
                        )
                    continue
                # Any other cascade method that branches on the guard is OK.
                if has_guard:
                    continue
                violations.append(
                    f"{path.relative_to(API_SRC)}::{cls.name}.{method.name}"
                )

    assert not violations, (
        "Repository read method hand-rolls an org-OR-global cascade instead of "
        "routing through _apply_cascade_scope — this bypasses self.is_external. "
        "Use the cascade primitive, branch on self.external_restricted, or "
        "allow-list with a reason + dedicated external test:\n  "
        + "\n  ".join(violations)
    )


# =============================================================================
# 3c. PATH lint: user-reachable router/service/mcp code must not hand-roll
#     the global arm against a model's organization_id.
# =============================================================================

# Execution-resolution entity models (api/src/repositories/README.md): these
# are the access-controlled, cascade-resolved entities an external user must
# NOT reach the global tier of. Identity-telemetry models (Execution*,
# WorkflowROIDaily, AIUsage, User, KnowledgeNamespaceRole) and the admin-only
# SystemConfig are deliberately excluded — their global rows are not
# user-facing execution-resolution data and are governed by the inline-scoping
# allow-list in test_org_scoping_enforcement.py.
_EXEC_RESOLUTION_MODELS = {
    "Workflow",
    "WorkflowORM",
    "Form",
    "FormORM",
    "Application",
    "ApplicationORM",
    "Agent",
    "AgentORM",
    "Table",
    "TableOrm",
    "TableORM",
    "ConfigModel",
    "Config",
    "KnowledgeStore",
    "IntegrationMapping",
    "MCPServer",
    "MCPConnection",
    "OAuthProvider",
    "OAuthToken",
}

# Files reachable by a CurrentActiveUser principal where an inline
# execution-resolution ``organization_id.is_(None)`` is verified external-safe.
# Entry is ``"<relative/path>:<reason>"`` — keep SHORT.
_PATH_GLOBAL_ARM_ALLOWLIST: dict[str, str] = {
    # knowledge_sources routes gate on is_external explicitly via
    # resolve_org_filter (external -> ORG_ONLY) — covered by e2e.
    "routers/knowledge_sources.py": "resolve_org_filter returns ORG_ONLY for externals (e2e)",
    # websocket _resolve_table_id drops the global arm for externals — CLEARED
    # by the review and covered by the existing external table test.
    "routers/websocket.py": "drops global arm when user.is_external (EXT-1 commit d0164ad8)",
    # SUPERUSER-ONLY routes (CurrentSuperuser dep): the global arm is reachable
    # only by a platform admin, never an external. Verified per-endpoint.
    "routers/workflows.py": "list_workflows is CurrentSuperuser-only",
    "routers/solutions.py": "solution mgmt endpoints are CurrentSuperuser-only",
    "routers/integrations.py": "integration config endpoints are CurrentSuperuser-only",
    "routers/tables.py": "_resolve_solution_table_by_name: install-scoped solution-app lookup, not an org cascade",
}


def _identifiers_in_module(tree: ast.Module) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def test_user_reachable_paths_dont_hand_roll_global_arm():
    """Router/service/mcp/tool code that authenticates a ``CurrentActiveUser``
    (or an MCP principal) and hand-rolls
    ``<ExecResolutionModel>.organization_id.is_(None)`` inline leaks the global
    tier to external users outside the repository (LEAK #3/#5 and siblings).

    Scoped to execution-resolution entity models so identity-telemetry and
    admin-only (SystemConfig) globals are not re-litigated here. Allow-listed
    files handle external-ness explicitly and say how.
    """
    roots = [API_SRC / "routers", API_SRC / "services"]
    violations: list[str] = []

    for root in roots:
        for path in root.rglob("*.py"):
            rel = str(path.relative_to(API_SRC))
            text = path.read_text()
            if "organization_id.is_(None)" not in text:
                continue
            tree = ast.parse(text)
            idents = _identifiers_in_module(tree)
            user_reachable = (
                bool({"CurrentActiveUser", "Context"} & idents)
                or "context.is_external" in text
                or "context.is_platform_admin" in text
            )
            if not user_reachable:
                continue
            if rel in _PATH_GLOBAL_ARM_ALLOWLIST:
                continue

            # A global arm is "guarded" only when it sits in the branch that
            # runs for a NON-external principal of an external-ness test.
            # POLARITY MATTERS — excusing both branches (or the non-admin body
            # of a bypass test) is how a leak slips back in:
            #   if is_external:        BODY=external (a global arm here LEAKS);
            #                          orelse=non-external (guarded).
            #   if not is_external:    BODY=non-external (guarded);
            #                          orelse=external (LEAKS).
            # Bypass tests (is_platform_admin / is_superuser) guard ONLY the
            # branch where the principal IS the bypass principal:
            #   if is_platform_admin:  BODY guarded; orelse NOT.
            #   if not is_platform_admin: BODY is the regular-user cascade and
            #                          is NOT auto-excused — it must carry its
            #                          own is_external guard.
            def _span(branch: list[ast.stmt]) -> list[tuple[int, int]]:
                return [
                    (s.lineno, getattr(s, "end_lineno", s.lineno)) for s in branch
                ]

            guarded_spans: list[tuple[int, int]] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                test_src = ast.unparse(node.test)
                negated = test_src.startswith("not ") or " not " in test_src
                is_ext_test = (
                    "is_external" in test_src or "external_restricted" in test_src
                )
                is_bypass_test = (
                    "is_platform_admin" in test_src or "is_superuser" in test_src
                )
                if is_ext_test:
                    # non-external branch is guarded.
                    guarded_spans += _span(node.orelse if not negated else node.body)
                elif is_bypass_test:
                    # only the IS-bypass branch is guarded.
                    guarded_spans += _span(node.body if not negated else node.orelse)

            def _is_guarded(lineno: int) -> bool:
                return any(lo <= lineno <= hi for lo, hi in guarded_spans)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if not (
                    isinstance(node.value, ast.Attribute)
                    and node.value.attr == "organization_id"
                    and node.attr == "is_"
                ):
                    continue
                base = node.value.value
                base_name = base.id if isinstance(base, ast.Name) else None
                if base_name not in _EXEC_RESOLUTION_MODELS:
                    continue
                if _is_guarded(node.lineno):
                    continue
                violations.append(f"{rel}:{node.lineno} ({base_name})")

    assert not violations, (
        "User-reachable router/service path hand-rolls "
        "<ExecResolutionModel>.organization_id.is_(None) — an external user "
        "reaching this code regains the global tier. Route through "
        "OrgScopedRepository (which honors is_external), gate the global arm on "
        "the caller's external-ness, or add the file to "
        "_PATH_GLOBAL_ARM_ALLOWLIST with a reason:\n  "
        + "\n  ".join(sorted(set(violations)))
    )
