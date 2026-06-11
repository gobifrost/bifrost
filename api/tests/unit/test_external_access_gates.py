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


# A literal ``is_superuser=True`` is sentinel trust. The lint above exempts it
# unconditionally — which is EXACTLY how OPEN-A/OPEN-B slipped: the SDK
# endpoints hardcoded ``is_superuser=True`` on a PLAIN ``CurrentUser`` route,
# so an external caller inherited the full cascade with no red build. This
# allow-list re-closes that hole: in a USER-reachable path (a router authing
# CurrentUser/CurrentActiveUser/Context, or an MCP tool), a literal
# ``is_superuser=True`` org-scoped repo construction must EITHER pass
# ``is_external`` alongside, OR be listed here with a verified reason naming
# the actual gate (a superuser-only route, or method-level external handling).
# Engine/sentinel/job paths are NOT user-reachable and never reach this lint.
#
# Key is ``"<relative/path>::<function>"``.
_LITERAL_SUPERUSER_USER_REACHABLE_ALLOWLIST: dict[str, str] = {
    # config router: every mutating/reading endpoint is CurrentSuperuser-gated
    # (src/routers/config.py imports CurrentSuperuser; verified per-endpoint).
    "routers/config.py::get_config": "CurrentSuperuser route",
    "routers/config.py::set_config": "CurrentSuperuser route",
    "routers/config.py::update_config": "CurrentSuperuser route",
    "routers/config.py::delete_config": "CurrentSuperuser route",
    # cli SDK config: external handling lives in the METHOD arg
    # (merged_for_sdk(external=current_user.is_external)) — EXT-1 NEW-1 — not
    # in the repo construction; the repo stays sentinel for decryption.
    "routers/cli.py::cli_get_config": "external gated via merged_for_sdk(external=...) (NEW-1)",
    "routers/cli.py::cli_list_config": "external gated via merged_for_sdk(external=...) (NEW-1)",
    # cli SDK integrations (EXT-1 OPEN-E): the OAuth/integration cascade now
    # passes is_external from the principal everywhere EXCEPT the DEFAULTS-path
    # OAuth-token lookup, which is reached ONLY when ``not current_user.is_external``
    # (the whole defaults OAuth block is gated off for externals — a global
    # third-party credential). So this one literal-sentinel construction is
    # external-unreachable by call-site.
    "routers/cli.py::sdk_integrations_get": "defaults-path OAuth lookup is guarded by `not is_external` (OPEN-E)",
    # tables router: create/list/update/delete are all CurrentSuperuser-gated.
    "routers/tables.py::create_table": "CurrentSuperuser route",
    "routers/tables.py::list_tables": "CurrentSuperuser route",
    "routers/tables.py::update_table": "CurrentSuperuser route",
    "routers/tables.py::delete_table": "CurrentSuperuser route",
    # mcp_servers router: server templates are platform-admin only.
    "routers/mcp_servers.py::update_mcp_server": "CurrentSuperuser route",
    "routers/mcp_servers.py::delete_mcp_server": "CurrentSuperuser route",
    # forms execute: the workflow repo is built sentinel ON THE FORM'S BEHALF
    # after the form's own access gate (which DOES carry is_external) already
    # authorized the caller — forms intentionally let users run workflows they
    # have no direct role on. Anchored to the form's org.
    "routers/forms.py::execute_form": "form access gate (is_external) authorizes; wf repo resolves on form's behalf",
    "routers/forms.py::execute_startup_workflow": "form access gate (is_external) authorizes; launch wf resolves on form's behalf",
    # oauth_connections router: every endpoint is CurrentSuperuser-gated.
    "routers/oauth_connections.py::get_connection": "CurrentSuperuser route",
    "routers/oauth_connections.py::create_connection": "CurrentSuperuser route",
    "routers/oauth_connections.py::update_connection": "CurrentSuperuser route",
    "routers/oauth_connections.py::delete_connection": "CurrentSuperuser route",
    "routers/oauth_connections.py::authorize_connection": "CurrentSuperuser route",
    "routers/oauth_connections.py::cancel_authorization": "CurrentSuperuser route",
    "routers/oauth_connections.py::refresh_token": "CurrentSuperuser route",
    "routers/oauth_connections.py::oauth_callback": "CurrentSuperuser route",
    "routers/oauth_connections.py::get_credentials": "CurrentSuperuser route",
    # MCP knowledge search: external handling is in the METHOD arg
    # (search(fallback=not is_external)); repo stays sentinel for embedding.
    "services/mcp_server/tools/knowledge.py::search_knowledge": "external gated via search(fallback=not is_external) (LEAK #6)",
    # MCP list_forms: the is_superuser=True branch is the is_platform_admin
    # arm (admins see all); the non-admin arms construct with is_superuser=False
    # and the FormRepository cascade honors external-ness there.
    "services/mcp_server/tools/forms.py::list_forms": "is_superuser=True branch is the platform-admin arm only",
}

_USER_REACHABLE_IDENTS = {"CurrentActiveUser", "CurrentUser", "Context"}


def _enclosing_function_names(tree: ast.Module) -> dict[int, str]:
    """Map a Call node's id() to the name of the function lexically enclosing
    it (best-effort — nested defs resolve to the innermost)."""
    out: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    out[id(node)] = fn.name
    return out


def test_literal_superuser_repo_in_user_path_requires_external_or_allowlist():
    """A literal ``is_superuser=True`` org-scoped repo construction is sentinel
    trust. In a USER-reachable router/service path it must NOT be auto-exempt —
    that auto-exemption is exactly what let OPEN-A/OPEN-B (SDK endpoints
    hardcoding ``is_superuser=True`` on a plain ``CurrentUser`` route) ship a
    cross-tenant leak with a green build.

    Such a site must either pass ``is_external`` alongside, OR be listed in
    ``_LITERAL_SUPERUSER_USER_REACHABLE_ALLOWLIST`` with a verified reason
    (a superuser-only route, or method-level external handling).
    """
    repo_names = _org_scoped_repo_class_names()
    roots = [API_SRC / "routers", API_SRC / "services"]

    violations: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            rel = str(path.relative_to(API_SRC)).replace("\\", "/")
            text = path.read_text()
            tree = ast.parse(text)
            idents = _identifiers_in_module(tree)
            is_mcp_tool = "services/mcp_server/tools/" in rel
            if not (_USER_REACHABLE_IDENTS & idents or is_mcp_tool):
                continue
            enclosing = _enclosing_function_names(tree)
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
                # Only LITERAL True is sentinel trust; literal False / dynamic
                # are handled by the principal-derived lint above.
                if not (isinstance(su, ast.Constant) and su.value is True):
                    continue
                if _is_external_value_present(node):
                    continue
                key = f"{rel}::{enclosing.get(id(node), '?')}"
                if key in _LITERAL_SUPERUSER_USER_REACHABLE_ALLOWLIST:
                    continue
                violations.append(f"{key} (line {node.lineno}) — {name}")

    assert not violations, (
        "A literal is_superuser=True org-scoped repo construction sits on a "
        "USER-reachable path with no is_external alongside and is not "
        "allow-listed. This is the OPEN-A/OPEN-B class — an external caller "
        "would inherit the full cascade. Pass is_external from the principal, "
        "or add the site to _LITERAL_SUPERUSER_USER_REACHABLE_ALLOWLIST with a "
        "verified reason naming its actual gate:\n  " + "\n  ".join(violations)
    )


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
# Maps each self-guarding method to a DEDICATED behavioral test file that
# proves the external path drops the global tier. The token-presence check
# above catches a fully stripped guard; these tests catch a logically broken
# guard (e.g. data-flow guards that AST spans can't see — NEW-1's external flag,
# knowledge.search's fallback flag). The meta-test below asserts each file
# exists, so removing a method's safety net can't pass silently.
_METHOD_SELF_GUARDING: dict[str, str] = {
    "agents.list_agents": "tests/unit/repositories/test_external_subclass_gates.py",
    "agents.get_agent_with_access_check": "tests/unit/repositories/test_external_subclass_gates.py",
    "forms.get_form_with_access_check": "tests/unit/repositories/test_external_subclass_gates.py",
    "knowledge.search": "tests/unit/test_external_leak_closures.py",
    "knowledge.list_namespaces": "tests/unit/test_external_leak_closures.py",
    "knowledge.list_documents_by_namespace": "tests/unit/test_external_leak_closures.py",
    "workflows.list_tools_for_filter": "tests/unit/test_external_leak_closures.py",
    # merged_for_sdk is reachable behind the PLAIN CurrentUser endpoint
    # /api/cli/config/get — NOT sentinel-only. EXT-1 NEW-1.
    "config.merged_for_sdk": "tests/unit/test_config_merged_external.py",
    # get_org_level_for_provider is reachable behind the PLAIN CurrentUser
    # endpoints /api/sdk/integrations/get + /refresh_token — NOT sentinel-only
    # (this exact false "SDK/engine" exemption is why OPEN-E slipped 3×). It
    # now branches on self.external_restricted.
    "oauth.get_org_level_for_provider": "tests/unit/test_integrations_external.py",
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


def _external_guarded_spans(scope: ast.AST) -> list[tuple[int, int]]:
    """Line spans within ``scope`` that run for a NON-external/bypass principal.

    POLARITY-AWARE (shared by the path lint and the method self-guard check):
      if is_external:        orelse guarded (body LEAKS)
      if not is_external:    body guarded   (orelse LEAKS)
      if is_platform_admin:  body guarded   (orelse NOT)
      if not is_platform_admin: orelse guarded (body is the regular cascade,
                             NOT auto-excused — must carry its own ext guard)
    A global arm sitting in a guarded span is recognized as gated.
    """

    def _span(branch: list[ast.stmt]) -> list[tuple[int, int]]:
        return [(s.lineno, getattr(s, "end_lineno", s.lineno)) for s in branch]

    def _is_ext_test(test: ast.expr) -> bool:
        src = ast.unparse(test)
        names = {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
        return (
            "is_external" in src
            or "external_restricted" in src
            or "external" in names
        )

    def _is_bypass_test(test: ast.expr) -> bool:
        src = ast.unparse(test)
        return "is_platform_admin" in src or "is_superuser" in src

    def _exits(branch: list[ast.stmt]) -> bool:
        # The branch unconditionally leaves the function (early-return guard).
        return any(isinstance(s, (ast.Return, ast.Raise)) for s in branch)

    spans: list[tuple[int, int]] = []
    # Branch-wrap guards (lexical if/else) — polarity-aware.
    for node in ast.walk(scope):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.unparse(node.test)
        negated = test_src.startswith("not ") or " not " in test_src
        if _is_ext_test(node.test):
            spans += _span(node.orelse if not negated else node.body)
        elif _is_bypass_test(node.test):
            spans += _span(node.body if not negated else node.orelse)

    # Early-return guards at FUNCTION scope: ``if <ext-test>: return …`` (or
    # ``if not <non-ext>: return``) means everything AFTER the if runs only for
    # a non-external principal. Recognize this so guard styles other than
    # if/else wrapping (e.g. AgentRepository.get_agent_with_access_check) count.
    for fn in ast.walk(scope):
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        body = fn.body
        for idx, stmt in enumerate(body):
            if not isinstance(stmt, ast.If):
                continue
            test_src = ast.unparse(stmt.test)
            negated = test_src.startswith("not ") or " not " in test_src
            # ``if external: return`` (positive ext-test, body exits) guards the
            # remainder. ``if not external: <run>`` is already a branch-wrap.
            if _is_ext_test(stmt.test) and not negated and _exits(stmt.body):
                rest = body[idx + 1 :]
                if rest:
                    spans.append(
                        (rest[0].lineno, getattr(rest[-1], "end_lineno", rest[-1].lineno))
                    )
    return spans


def _unguarded_global_arm_lines(
    scope: ast.AST, models: set[str] | None = None
) -> list[int]:
    """Lines in ``scope`` where ``<Model>.organization_id.is_(None)`` sits
    OUTSIDE an external-guarded branch. ``models=None`` matches any base."""
    guarded = _external_guarded_spans(scope)

    def _is_guarded(ln: int) -> bool:
        return any(lo <= ln <= hi for lo, hi in guarded)

    out: list[int] = []
    for node in ast.walk(scope):
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
        if models is not None and base_name not in models:
            continue
        if _is_guarded(node.lineno):
            continue
        out.append(node.lineno)
    return out


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
                # Guard tokens in the CODE only — a mention in the docstring
                # must NOT count (else a stripped guard with a stale docstring
                # passes). Drop a leading string-expr (docstring) before unparse.
                body = method.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body = body[1:]
                src = "\n".join(ast.unparse(s) for s in body)
                has_guard = "external_restricted" in src or "is_external" in src
                if key in _METHOD_SELF_GUARDING:
                    # The self-guard token must be present (catches a fully
                    # stripped guard — LEAK #4's failure mode). A guard that is
                    # present but logically broken (e.g. NEW-1's external=False)
                    # is NOT structurally detectable here — those methods carry
                    # a DEDICATED behavioral test (asserted to exist + pass by
                    # ``test_self_guarding_methods_have_behavioral_coverage``),
                    # which is the real safety net for data-flow guards.
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


def test_self_guarding_methods_have_behavioral_coverage():
    """Every self-guarding cascade method must name an EXISTING behavioral test
    that proves its external path drops the global tier. This is the safety net
    for data-flow guards the structural lint can't verify (NEW-1's external
    flag, knowledge.search's fallback flag). Deleting a method's covering test
    fails here instead of silently un-protecting it."""
    repo_root = API_SRC.parent
    missing = [
        f"{method} -> {test_path}"
        for method, test_path in _METHOD_SELF_GUARDING.items()
        if not (repo_root / test_path).exists()
    ]
    assert not missing, (
        "Self-guarding method names a behavioral test file that does not "
        "exist:\n  " + "\n  ".join(missing)
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
    # EventSource: a plain BaseRepository, but its MCP tools (events.py) are
    # user-reachable and hand-rolled org/global filters → cross-org/global leak
    # (EXT-1 NEW-2). Included so the path-lint covers regressions there.
    "EventSource",
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
    # NOTE: routers/tables.py is NO LONGER whole-file exempt (EXT-1 NEW-3) — the
    # _resolve_solution_table_by_name global arm is now polarity-guarded by
    # is_external, so the path-lint recognizes it directly.
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
            # User-reachable surfaces: a REST endpoint authing CurrentActiveUser/
            # Context, OR an MCP tool (authenticates as the user). MCP tools live
            # under services/mcp_server/tools/ and read scope off ``context``
            # (often via ``getattr(context, …)``), so the literal-text checks
            # alone miss them — include the whole MCP-tools directory.
            is_mcp_tool = "services/mcp_server/tools/" in rel.replace("\\", "/")
            user_reachable = (
                bool({"CurrentActiveUser", "Context"} & idents)
                or "context.is_external" in text
                or "context.is_platform_admin" in text
                or 'getattr(context, "is_external"' in text
                or 'getattr(context, "is_platform_admin"' in text
                or is_mcp_tool
            )
            if not user_reachable:
                continue
            if rel in _PATH_GLOBAL_ARM_ALLOWLIST:
                continue

            # Polarity-aware global-arm detection (shared helper): a global arm
            # outside an external-guarded branch leaks. The model filter limits
            # to execution-resolution entities.
            for lineno in _unguarded_global_arm_lines(tree, _EXEC_RESOLUTION_MODELS):
                violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "User-reachable router/service path hand-rolls "
        "<ExecResolutionModel>.organization_id.is_(None) — an external user "
        "reaching this code regains the global tier. Route through "
        "OrgScopedRepository (which honors is_external), gate the global arm on "
        "the caller's external-ness, or add the file to "
        "_PATH_GLOBAL_ARM_ALLOWLIST with a reason:\n  "
        + "\n  ".join(sorted(set(violations)))
    )


# =============================================================================
# 3d. EXTERNAL-FLAG lint: BaseRepository config-merge methods carry a global
#     tier behind an ``external=`` flag (not the OrgScoped cascade the
#     method-lint sees). A user-reachable router calling them WITHOUT
#     external= (now a required arg) or with ``external=False`` LITERAL on a
#     plain-user path is a leak — this is the NEW-G/OPEN-E class (four sibling
#     SDK endpoints, one missed flag = a portal user reads another tenant's
#     decrypted secrets).
# =============================================================================

# Methods that merge/return the GLOBAL (org_id=NULL) integration tier and gate
# it on an ``external=`` keyword. Any user-reachable caller MUST pass
# ``external=<principal>.is_external`` (NOT a False literal) unless allow-listed.
_EXTERNAL_FLAG_METHODS = {
    "get_config_for_mapping",
    "get_integration_defaults",
}

# Call sites that legitimately pass ``external=False`` because the route is
# SUPERUSER-only (no external principal ever reaches them). Key is
# ``"<relative/path>::<function>"`` with a verified reason.
_EXTERNAL_FLAG_FALSE_ALLOWLIST: dict[str, str] = {
    # integrations.py admin config routes are all CurrentSuperuser-gated.
    "routers/integrations.py::get_integration": "CurrentSuperuser route",
    "routers/integrations.py::update_integration_config": "CurrentSuperuser route",
    "routers/integrations.py::get_integration_config": "CurrentSuperuser route",
    # The local IntegrationsRepository.get_config_for_mapping delegates to
    # get_integration_defaults(external=external) — it FORWARDS the flag, it
    # does not hardcode False. (Listed so the forwarding call is explicit.)
    "routers/integrations.py::get_config_for_mapping": "forwards external= to get_integration_defaults",
}


def _external_kwarg(node: ast.Call) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg == "external":
            return kw.value
    return None


def test_user_reachable_callers_pass_external_flag():
    """Every user-reachable call to a global-tier config-merge method
    (get_config_for_mapping / get_integration_defaults) must pass
    ``external=<principal>.is_external`` — NOT omit it (now a required arg) and
    NOT pass a ``False`` literal on a plain-user path. A ``False`` literal is
    allowed only at an allow-listed superuser-only call site.

    This is the structural backstop for the NEW-G/OPEN-E class: the methods sit
    on a BaseRepository (not OrgScoped), so the cascade method-lint can't see
    their call sites. Without this, a 5th sibling SDK endpoint that forgets the
    flag (or copy-pastes external=False) silently re-opens the global secret
    tier to a hospital portal user.
    """
    roots = [API_SRC / "routers", API_SRC / "services"]
    violations: list[str] = []

    for root in roots:
        for path in root.rglob("*.py"):
            rel = str(path.relative_to(API_SRC)).replace("\\", "/")
            text = path.read_text()
            if not any(m in text for m in _EXTERNAL_FLAG_METHODS):
                continue
            tree = ast.parse(text)
            idents = _identifiers_in_module(tree)
            is_mcp_tool = "services/mcp_server/tools/" in rel
            user_reachable = (
                bool({"CurrentActiveUser", "CurrentUser", "Context"} & idents)
                or is_mcp_tool
            )
            if not user_reachable:
                continue
            enclosing = _enclosing_function_names(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else None
                if name not in _EXTERNAL_FLAG_METHODS:
                    continue
                key = f"{rel}::{enclosing.get(id(node), '?')}"
                ext = _external_kwarg(node)
                if ext is None:
                    # Required arg omitted — a runtime TypeError AND a leak risk.
                    violations.append(f"{key} (line {node.lineno}) — {name}() missing external=")
                    continue
                is_false_literal = (
                    isinstance(ext, ast.Constant) and ext.value is False
                )
                if is_false_literal and key not in _EXTERNAL_FLAG_FALSE_ALLOWLIST:
                    violations.append(
                        f"{key} (line {node.lineno}) — {name}(external=False) "
                        f"on a user-reachable path (not allow-listed)"
                    )

    assert not violations, (
        "A user-reachable router/service calls a global-tier config-merge "
        "method without threading the principal's is_external (NEW-G/OPEN-E "
        "class). Pass external=<principal>.is_external, or — only for a "
        "superuser-only route — external=False with an entry in "
        "_EXTERNAL_FLAG_FALSE_ALLOWLIST:\n  " + "\n  ".join(sorted(set(violations)))
    )
