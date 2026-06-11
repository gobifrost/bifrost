"""Mechanical enforcement of the org-scoping pattern.

Three tests:

1. ``test_no_inline_org_scoping_in_routers`` — no raw ``organization_id ==``
   or ``organization_id.is_(None)`` outside the canonical
   ``OrgScopedRepository`` base class. Allow-listed lines are exempt with a
   one-line justification.

2. ``test_org_scoped_models_have_repository`` — every ORM model with an
   ``organization_id`` column either has an ``OrgScopedRepository``
   subclass or is on the explicit identity-entity allow-list.

3. ``test_sdk_endpoints_use_resolver`` — every SDK endpoint that accepts a
   ``scope`` parameter calls ``resolve_effective_scope``. Allow-listed
   endpoints document why they're exempt.

These tests are the load-bearing mechanism that prevents the pattern from
drifting. Allow-lists shrink as code migrates. When you add an entry,
include a comment explaining why — code review enforces accountability.

See ``api/src/repositories/README.md`` for the full pattern.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

API_ROOT = Path(__file__).resolve().parents[2] / "src"
ROUTERS_DIR = API_ROOT / "routers"
MODELS_ORM_DIR = API_ROOT / "models" / "orm"
REPOSITORIES_DIR = API_ROOT / "repositories"


# ---------------------------------------------------------------------------
# Test 1: No inline org scoping in routers
# ---------------------------------------------------------------------------

# Pattern: "<something>.organization_id == ..." or
# "<something>.organization_id.is_(None)" or
# "<something>.organization_id.in_(...)".
#
# We match the operator forms used in SQLAlchemy expressions. Comments and
# string literals are NOT excluded by the regex — we lean on the allow-list
# to handle those. (A python-ast-aware approach would be cleaner but the
# allow-list approach keeps the test cheap and the violation set visible.)
_INLINE_ORG_RE = re.compile(
    r"\b\w+\.organization_id\s*(?:==|\.is_\s*\(|\.in_\s*\()"
)

# Lines that are exempt from the no-inline rule. Keyed by content, not
# line number — line numbers are too fragile under any refactor that
# adds/removes lines above. Each entry is
# ``(file_relative_to_api_root, line_content_stripped, reason)``.
#
# The goal of phases 4-7 is to SHRINK this list. Adding a new entry should
# be rare and obvious in code review. Removing an entry signals migration
# progress.
ALLOW_LIST_INLINE_ORG: set[tuple[str, str, str]] = {
    ('routers/agents.py', 'MCPConnection.organization_id == agent_data.organization_id,', 'agents MCPConnection lookup; phase 6 migrates via MCPConnectionRepository'),
    # ApplicationRepository entries removed in phase 6 — repository relocated
    # from routers/applications.py to repositories/applications.py.
    ('routers/claims.py', 'Table.organization_id == org_id, Table.name == table_name', 'claims inline lookups; phase 6 migrates via CustomClaimRepository'),
    ('routers/claims.py', 'ClaimORM.organization_id == org_id, ClaimORM.name.in_(refs)', 'claims inline lookups; phase 6 migrates via CustomClaimRepository'),
    ('routers/claims.py', 'select(ClaimORM).where(ClaimORM.organization_id == org_id)', 'claims inline lookups; phase 6 migrates via CustomClaimRepository'),
    ('routers/claims.py', 'Table.organization_id == org_id, Table.access.is_not(None)', 'claims inline lookups; phase 6 migrates via CustomClaimRepository'),
    ('routers/claims.py', 'stmt = stmt.where(ClaimORM.organization_id == filter_org)', 'claims inline lookups; phase 6 migrates via CustomClaimRepository'),
    ('routers/claims.py', 'ClaimORM.organization_id == org_id, ClaimORM.name == name', 'claims inline lookups; phase 6 migrates via CustomClaimRepository'),
    ('routers/cli.py', 'ConfigModel.organization_id == org_uuid,', 'cli config inline; phase 5 migrates'),
    ('routers/cli.py', 'Table.organization_id == org_uuid,', 'cli_create_table exact-scope uniqueness check (NOT cascade)'),
    # cli_list_tables migrated to TableRepository.list() in phase 6.
    ('routers/executions.py', 'query = query.where(ExecutionModel.organization_id == org_id)', 'Execution identity-entity filter (permanent)'),
    ('routers/export_import.py', 'Config.organization_id == mapping.organization_id', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'else Config.organization_id.is_(None),', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'Config.organization_id.is_(None),', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'existing_query = existing_query.where(KnowledgeStore.organization_id == org_id)', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'existing_query = existing_query.where(KnowledgeStore.organization_id.is_(None))', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'existing_query = existing_query.where(Table.organization_id == org_id)', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'existing_query = existing_query.where(Table.organization_id.is_(None))', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'existing_query = existing_query.where(Config.organization_id == org_id)', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'existing_query = existing_query.where(Config.organization_id.is_(None))', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'mapping_query = mapping_query.where(IntegrationMapping.organization_id == org_id)', 'manifest sync inline; phase 8 follow-up'),
    ('routers/export_import.py', 'mapping_query = mapping_query.where(IntegrationMapping.organization_id.is_(None))', 'manifest sync inline; phase 8 follow-up'),
    ('routers/integrations.py', 'IntegrationMapping.organization_id == org_id,', 'integration mapping inline; phase 6 migrates'),
    ('routers/integrations.py', 'ConfigModel.organization_id.is_(None),', 'integration config inline; phase 5 migrates'),
    ('routers/integrations.py', 'ConfigModel.organization_id == organization_id,', 'integration config inline; phase 5 migrates'),
    ('routers/integrations.py', 'ConfigModel.organization_id == org_id,', 'integration config inline; phase 5 migrates'),
    ('routers/knowledge_sources.py', 'KnowledgeNamespaceRole.organization_id == org_id,', 'knowledge sources inline cascade; phase 6 migrates'),
    # The KnowledgeStore document-list inline cascades were removed (EXT-1
    # NEW-J) — list_all_documents / list_documents now route through
    # org_filter_clause (the single source of truth that honors the EMPTY
    # sentinel). Only the namespace-role + target_org_id lookups remain inline.
    ('routers/knowledge_sources.py', 'KnowledgeStore.organization_id == target_org_id,', 'knowledge sources inline cascade; phase 6 migrates'),
    ('routers/llm_config.py', 'SystemConfig.organization_id.is_(None),', 'SystemConfig admin lookup; pre-repo pattern (permanent)'),
    ('routers/mcp_connections.py', 'query = query.where(MCPConnection.organization_id == scope_org)', 'MCP connection org filter; phase 6 migrates'),
    ('routers/metrics.py', 'query = query.where(ExecutionMetricsDaily.organization_id == org_uuid)', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    ('routers/metrics.py', 'query = query.where(ExecutionMetricsDaily.organization_id.is_(None))', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    ('routers/metrics.py', '.join(Organization, ExecutionMetricsDaily.organization_id == Organization.id)', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    ('routers/metrics.py', '.where(ExecutionMetricsDaily.organization_id.is_(None))', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    # OAuthConnectionRepository deleted in the resumed phase 4/scope-cleanup pass.
    # All 5 OAuthProvider/Token inline-cascade entries in oauth_connections.py
    # disappeared with the class. The new OAuthProviderRepository in
    # api/src/repositories/oauth.py is the canonical class.
    ('routers/oauth_connections.py', 'SystemConfig.organization_id.is_(None),  # Global system config', 'SystemConfig admin lookup; pre-repo pattern (permanent)'),
    ('routers/oauth_connections.py', 'SystemConfig.organization_id.is_(None),', 'SystemConfig admin lookup; pre-repo pattern (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(ExecutionMetricsDaily.organization_id.is_(None))', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(ExecutionMetricsDaily.organization_id == org_uuid)', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(WorkflowROIDaily.organization_id.is_(None))', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(WorkflowROIDaily.organization_id == org_uuid)', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', '.join(Organization, ExecutionMetricsDaily.organization_id == Organization.id)', 'identity-entity scope filter (permanent)'),
    ('routers/roles.py', 'KnowledgeNamespaceRoleORM.organization_id == entry.organization_id,', 'KnowledgeNamespaceRole identity-entity filter (permanent)'),
    ('routers/solutions.py', 'set_keys_q = select(Config.key).where(Config.organization_id == sol.organization_id)', 'entities endpoint: install-scoped config-key existence read for value_set status (NOT cascade)'),
    ('routers/solutions.py', 'set_keys_q = select(Config.key).where(Config.organization_id.is_(None))', 'entities endpoint: global-scope config-key existence read for value_set status (NOT cascade)'),
    ('routers/solutions.py', 'Config.organization_id == sol.organization_id', 'uninstall: install-scoped orphan-stamp UPDATE on config values (NOT cascade)'),
    ('routers/solutions.py', 'else Config.organization_id.is_(None)', 'uninstall: global-scope orphan-stamp UPDATE on config values (NOT cascade)'),
    ('routers/solutions.py', 'SolutionORM.organization_id == sol.organization_id', 'uninstall: find OTHER live installs declaring the same key in this org to skip stamping a shared value (NOT cascade)'),
    ('routers/solutions.py', 'else SolutionORM.organization_id.is_(None)', 'uninstall: global-scope variant of the same shared-key guard (NOT cascade)'),
    ('routers/tables.py', 'CustomClaimORM.organization_id == organization_id', 'tables custom claim cross-ref; phase 6 migrates'),
    # X-Bifrost-App app-scoped solution table lookup (install-scoped, NOT org
    # cascade).
    ('routers/tables.py', 'Table.organization_id == target_org_id,', 'install-scoped solution table lookup: org arm'),
    ('routers/tables.py', 'Table.organization_id.is_(None),', 'install-scoped solution table lookup: global arm'),
    ('routers/usage_reports.py', 'base_conditions.append(AIUsage.organization_id == filter_org_id)', 'identity-entity scope filter (permanent)'),
    ('routers/usage_reports.py', 'exec_conditions.append(Execution.organization_id == filter_org_id)', 'identity-entity scope filter (permanent)'),
    ('routers/usage_reports.py', 'workflow_query = workflow_query.where(AIUsage.organization_id == filter_org_id)', 'identity-entity scope filter (permanent)'),
    ('routers/usage_reports.py', 'conv_query = conv_query.where(AIUsage.organization_id == filter_org_id)', 'identity-entity scope filter (permanent)'),
    ('routers/usage_reports.py', 'agent_query = agent_query.where(AIUsage.organization_id == filter_org_id)', 'identity-entity scope filter (permanent)'),
    ('routers/usage_reports.py', '.join(Organization, AIUsage.organization_id == Organization.id)', 'identity-entity scope filter (permanent)'),
    ('routers/usage_reports.py', 'Organization, KnowledgeStorageDaily.organization_id == Organization.id', 'identity-entity scope filter (permanent)'),
    ('routers/usage_reports.py', 'KnowledgeStorageDaily.organization_id == filter_org_id', 'identity-entity scope filter (permanent)'),
    ('routers/users.py', 'query = query.where(UserORM.organization_id.is_(None))', 'User identity-entity filter (permanent)'),
    ('routers/users.py', 'query = query.where(UserORM.organization_id == filter_org)', 'User identity-entity filter (permanent)'),
    ('routers/websocket.py', '(TableOrm.organization_id == user.organization_id)', 'websocket table subscription filter; phase 6 migrates'),
    ('routers/websocket.py', '| TableOrm.organization_id.is_(None)', 'websocket table subscription filter; phase 6 migrates'),
    ('routers/workflows.py', 'query = query.where(WorkflowORM.organization_id.is_(None))', 'workflows inline cascade; phase 6 migrates'),
    ('routers/workflows.py', 'query = query.where(WorkflowORM.organization_id == filter_org)', 'workflows inline cascade; phase 6 migrates'),
    ('routers/workflows.py', 'WorkflowORM.organization_id == filter_org,', 'workflows inline cascade; phase 6 migrates'),
    ('routers/workflows.py', 'WorkflowORM.organization_id.is_(None),', 'workflows inline cascade; phase 6 migrates'),
    ('routers/workflows.py', 'forms_query = forms_query.where(Form.organization_id == org_filter)', 'workflows inline cascade; phase 6 migrates'),
    ('routers/workflows.py', 'agents_query = agents_query.where(Agent.organization_id == org_filter)', 'workflows inline cascade; phase 6 migrates'),
    ('routers/workflows.py', 'apps_base_query = apps_base_query.where(Application.organization_id == org_filter)', 'workflows inline cascade; phase 6 migrates'),
}


def _scan_file_for_inline_org(file_path: Path) -> list[tuple[int, str]]:
    """Return (line_number, content) for every inline org reference."""
    findings: list[tuple[int, str]] = []
    text = file_path.read_text()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _INLINE_ORG_RE.search(line):
            findings.append((line_number, line.strip()))
    return findings


class TestNoInlineOrgScopingInRouters:
    """Routers MUST NOT contain raw organization_id comparisons.

    Use OrgScopedRepository. The cascade primitive lives in the base class.
    """

    def test_routers_have_no_unallowlisted_inline_org_filters(self) -> None:
        # Build a (file, content) -> reason map from the allow-list. Content
        # match means line-number reshuffling doesn't break the test; only
        # actual NEW code introduces a violation.
        allowed: set[tuple[str, str]] = {
            (entry[0], entry[1]) for entry in ALLOW_LIST_INLINE_ORG
        }

        violations: list[tuple[str, int, str]] = []
        for py_file in sorted(ROUTERS_DIR.rglob("*.py")):
            rel = py_file.relative_to(API_ROOT).as_posix()
            for line_number, content in _scan_file_for_inline_org(py_file):
                if (rel, content) not in allowed:
                    violations.append((rel, line_number, content))

        if violations:
            details = "\n".join(
                f"  {file}:{line}  {content}" for file, line, content in violations
            )
            pytest.fail(
                "Found inline organization_id filters in routers that are not on "
                "the allow-list. Either migrate the code to OrgScopedRepository "
                "(see api/src/repositories/README.md) or, if legitimate, add an "
                "allow-list entry (file, stripped-content, reason) with a "
                f"one-line justification:\n{details}"
            )

    def test_allowlist_entries_still_exist(self) -> None:
        """If an allow-list entry no longer matches any code in the named
        file, it's stale and should be removed.

        This is what makes shrinking the list a positive signal: migrating
        an entry's line out of the codebase forces the allow-list entry
        to be deleted too (otherwise this test fails).
        """
        # Build (file, content) set from current scan.
        current_violations: set[tuple[str, str]] = set()
        for py_file in sorted(ROUTERS_DIR.rglob("*.py")):
            rel = py_file.relative_to(API_ROOT).as_posix()
            for _line, content in _scan_file_for_inline_org(py_file):
                current_violations.add((rel, content))

        stale: list[tuple[str, str, str]] = []
        for file_rel, content, reason in ALLOW_LIST_INLINE_ORG:
            if (file_rel, content) not in current_violations:
                stale.append((file_rel, content, reason))

        if stale:
            details = "\n".join(
                f"  ({file!r}, {content!r}, {reason!r})"
                for file, content, reason in stale
            )
            pytest.fail(
                "Allow-list entries that no longer match any code in the named "
                "file. Remove them from ALLOW_LIST_INLINE_ORG — they're stale.\n"
                f"{details}"
            )


# ---------------------------------------------------------------------------
# Test 2: Org-scoped models have a repository
# ---------------------------------------------------------------------------


# Models that are identity entities — they carry organization_id but are NOT
# resolved by cascade. They do NOT need an OrgScopedRepository subclass.
# See api/src/repositories/README.md for the classification table.
IDENTITY_MODELS: set[str] = {
    "Execution",
    "ExecutionMetricsDaily",
    "WorkflowROIDaily",
    "KnowledgeStorageDaily",
    "User",
    "AIUsage",
    "KnowledgeNamespaceRole",
    "Event",
    "AuditLog",
    # A Solution install belongs to a scope (organization_id) but is never
    # resolved by name with cascade — it is identity, like Organization.
    "Solution",
}


# Models that ARE execution-resolution and must have an OrgScopedRepository
# subclass. The phase-4-onward migrations create the repos that aren't here
# yet; until they exist, those models are allow-listed.
EXECUTION_RESOLUTION_MODELS_WITHOUT_REPO_YET: set[str] = {
    "OAuthProvider",     # Phase 4: OAuthProviderRepository
    "OAuthToken",        # Phase 4: OAuthTokenRepository
    "SystemConfig",      # Future: SystemConfigRepository (post-phase-5)
    "EventSource",       # Future: EventSourceRepository (post-phase-6)
    "CustomClaim",       # Future: CustomClaimRepository (post-phase-6)
    "MCPConnection",     # Existing MCPConnectionRepository but check confirms
    "MCPServer",         # Existing MCPServerRepository but check confirms
}


def _models_with_org_id() -> dict[str, Path]:
    """Return {ClassName: file_path} for every Base subclass that declares
    an organization_id column."""
    found: dict[str, Path] = {}
    for py_file in sorted(MODELS_ORM_DIR.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Must be a Base subclass (any base named "Base")
            is_base_subclass = any(
                isinstance(b, ast.Name) and b.id == "Base"
                for b in node.bases
            )
            if not is_base_subclass:
                continue

            for stmt in node.body:
                # Looking for `organization_id: Mapped[...] = mapped_column(...)`.
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.target.id == "organization_id":
                        found[node.name] = py_file
                        break

    return found


def _file_import_aliases(tree: ast.AST) -> dict[str, str]:
    """Return {local_name: original_name} for every `from X import Y as Z`."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
    return aliases


def _repository_subclasses() -> set[str]:
    """Return the set of ORM model names that have an OrgScopedRepository
    subclass somewhere in api/src/. Resolves `import X as Y` aliases so a
    declaration like `OrgScopedRepository[FormORM]` (where FormORM is
    `Form` aliased) maps back to `Form`."""
    bound: set[str] = set()
    for py_file in API_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue

        aliases = _file_import_aliases(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                if (
                    isinstance(base, ast.Subscript)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "OrgScopedRepository"
                    and isinstance(base.slice, ast.Name)
                ):
                    type_param = base.slice.id
                    # If the type-param was an alias, resolve to the original.
                    bound.add(aliases.get(type_param, type_param))
    return bound


class TestOrgScopedModelsHaveRepository:
    """Every ORM model with organization_id must be classified.

    Either it has an OrgScopedRepository subclass (execution-resolution),
    OR it's on the IDENTITY_MODELS allow-list (identity entity).

    Adding an org-scoped model without classification fails this test.
    """

    def test_all_org_id_models_classified(self) -> None:
        models_with_org_id = _models_with_org_id()
        repos = _repository_subclasses()

        unclassified: list[tuple[str, str]] = []
        for model_name, file_path in models_with_org_id.items():
            if model_name in IDENTITY_MODELS:
                continue
            if model_name in repos:
                continue
            if model_name in EXECUTION_RESOLUTION_MODELS_WITHOUT_REPO_YET:
                continue
            unclassified.append((model_name, file_path.relative_to(API_ROOT).as_posix()))

        if unclassified:
            details = "\n".join(
                f"  {model} in {file}" for model, file in unclassified
            )
            pytest.fail(
                "ORM models with organization_id that are not classified.\n"
                "Either:\n"
                "  - Add an OrgScopedRepository subclass (if execution-resolution), or\n"
                "  - Add the model name to IDENTITY_MODELS in this test (if identity).\n"
                "See api/src/repositories/README.md for the classification rule.\n"
                f"Unclassified models:\n{details}"
            )

    def test_identity_models_actually_exist(self) -> None:
        """If an entry in IDENTITY_MODELS no longer exists in the ORM,
        the allow-list has drifted and needs cleanup."""
        models_with_org_id = set(_models_with_org_id().keys())
        stale = IDENTITY_MODELS - models_with_org_id
        if stale:
            pytest.fail(
                "IDENTITY_MODELS contains entries that no longer have "
                f"organization_id columns (or no longer exist): {sorted(stale)}. "
                "Remove them from the allow-list."
            )

    def test_no_overlap_between_buckets(self) -> None:
        """A model can't be both identity and execution-resolution."""
        overlap = IDENTITY_MODELS & EXECUTION_RESOLUTION_MODELS_WITHOUT_REPO_YET
        assert not overlap, (
            f"Models classified as BOTH identity and execution-resolution: {overlap}. "
            "Pick one."
        )


# ---------------------------------------------------------------------------
# Test 3: SDK endpoints that accept `scope` call resolve_effective_scope
# ---------------------------------------------------------------------------


SDK_ROUTER_FILES = {
    # When phase 7 renames /api/sdk/* to /api/sdk/*, update this list.
    ROUTERS_DIR / "cli.py",
}


# Handlers under /api/sdk/* that are exempt from the
# "must call _resolve_sdk_org_id or resolve_effective_scope" rule. Keyed on
# handler function name (qualname is fine too but FastAPI handlers are
# module-level so the simple name is unambiguous). The key TRAVELS with
# the function across URL renames; the path-based version would have
# missed the same drift the resolver test was meant to catch.
EXEMPT_SDK_HANDLERS: dict[str, str] = {
    # As of 2026-05-26 every scope-taking SDK handler in cli.py routes
    # through _resolve_sdk_org_id. This list is left in place so that future
    # exempt handlers can be added with an explicit justification — adding
    # an entry without a one-line reason is itself a CI failure
    # (test_exempt_list_well_formed).
}


# Names that count as "calling the resolver" — direct call to
# resolve_effective_scope, or call to the thin _resolve_sdk_org_id wrapper.
RESOLVER_CALL_NAMES = {"resolve_effective_scope", "_resolve_sdk_org_id"}


def _handler_names_taking_scope(tree: ast.AST) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    """Find every router-decorated async handler that takes a ``scope`` arg
    (either directly in the function signature or via a Pydantic ``request``
    body that declares ``scope`` — the latter is the common pattern in
    cli.py)."""
    handlers: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        # Only handlers decorated with @router.<verb>(...)
        is_route = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in node.decorator_list
        )
        if not is_route:
            continue
        handlers[node.name] = node
    return handlers


def _handler_uses_request_scope(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Return True if the handler body references ``request.scope`` —
    that's how scope flows in for nearly every cli.py endpoint."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Attribute)
            and sub.attr == "scope"
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "request"
        ):
            return True
    return False


def _handler_signature_takes_scope(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Return True if ``scope`` appears as a direct function parameter."""
    args = node.args
    for a in (*args.args, *args.kwonlyargs, *(args.posonlyargs or [])):
        if a.arg == "scope":
            return True
    return False


# Pydantic contract files we statically inspect for scope-bearing models.
# When a handler's body parameter is annotated with one of these models,
# and that model declares a `scope` field, the handler is scope-taking
# even if the body never references `request.scope` directly. This is
# the third tripwire the post-Codex hardening needed — without it a
# future endpoint could accept ``SomeRequest(scope: str | None)`` and
# never read it, slipping past the body-walk check.
CONTRACT_FILES = (API_ROOT / "models" / "contracts" / "cli.py",)


def _models_with_scope_field() -> set[str]:
    """Return the set of Pydantic model class names declaring a ``scope``
    field in the SDK contract files. Static AST inspection only — no
    runtime import (avoids pulling FastAPI / DB deps into a unit test).
    """
    models: set[str] = set()
    for path in CONTRACT_FILES:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                # ``scope: str | None = Field(...)`` is an AnnAssign with
                # target.id == "scope". That's all we need.
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.target.id == "scope":
                        models.add(node.name)
                        break
    return models


def _handler_body_annotation_has_scope(
    node: ast.AsyncFunctionDef | ast.FunctionDef,
    scope_models: set[str],
) -> bool:
    """Return True if the handler's signature includes a parameter whose
    annotation references a model that declares a ``scope`` field.

    Detects both bare names (``request: SDKFooRequest``) and attribute
    forms (``request: contracts.SDKFooRequest``). False positives are
    OK — the contract is "scope-taking handlers must call the resolver";
    a handler with a scope-bearing body that doesn't use scope still
    benefits from declaring the gate or going on the exempt list.
    """
    args = node.args
    all_args = (*args.args, *args.kwonlyargs, *(args.posonlyargs or []))
    for a in all_args:
        ann = a.annotation
        if ann is None:
            continue
        # Bare name annotation, e.g. ``request: SDKIntegrationsGetRequest``
        if isinstance(ann, ast.Name) and ann.id in scope_models:
            return True
        # Attribute annotation, e.g. ``request: contracts.SDKFoo``
        if isinstance(ann, ast.Attribute) and ann.attr in scope_models:
            return True
        # Subscript / generic / union — walk inner names too.
        for sub in ast.walk(ann):
            if isinstance(sub, ast.Name) and sub.id in scope_models:
                return True
            if isinstance(sub, ast.Attribute) and sub.attr in scope_models:
                return True
    return False


def _handler_calls_resolver(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Walk the handler body looking for a call to one of the resolver
    functions. Both bare-name and attribute access count (e.g. ``shared.
    scope_resolver.resolve_effective_scope(...)`` would still resolve via
    the attribute's ``.attr``)."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name) and func.id in RESOLVER_CALL_NAMES:
                return True
            if isinstance(func, ast.Attribute) and func.attr in RESOLVER_CALL_NAMES:
                return True
    return False


class TestSDKEndpointsUseResolver:
    """Every SDK handler that takes a ``scope`` (either as a direct
    parameter or via the ``request.scope`` pattern that is standard in
    cli.py) must call ``_resolve_sdk_org_id`` or ``resolve_effective_scope``.

    The original placeholder (pre-2026-05-26 audit) only verified the
    exempt list was well-formed and the resolver module imported. That
    laxity is exactly what let four ``/api/sdk/integrations/*_mapping``
    handlers ship without any scope gate at all. This version walks each
    handler's AST and fails the build if a non-exempt handler doesn't
    invoke a resolver.

    Allow-list semantics:
      - Keyed on handler function name (travels through URL renames).
      - One-line justification per entry.
      - Adding an entry should require code review; that is the
        accountability mechanism.
    """

    def test_resolver_module_importable(self) -> None:
        from shared.scope_resolver import resolve_effective_scope

        assert callable(resolve_effective_scope)

    def test_exempt_list_well_formed(self) -> None:
        for name, reason in EXEMPT_SDK_HANDLERS.items():
            assert name.isidentifier(), (
                f"Exempt entry must be a Python identifier (handler function name): {name!r}"
            )
            assert reason, f"Exempt handler {name} needs a one-line justification"

    def test_sdk_router_files_exist(self) -> None:
        for path in SDK_ROUTER_FILES:
            assert path.exists(), f"SDK router file missing: {path}"

    def test_every_scope_taking_handler_calls_resolver(self) -> None:
        """The load-bearing assertion. For each SDK router file, find every
        @router-decorated handler that accepts a scope (direct parameter,
        ``request.scope`` body access, OR a Pydantic body annotation whose
        model declares a ``scope`` field). If it does, the handler body
        must call ``_resolve_sdk_org_id`` or ``resolve_effective_scope``
        unless it's on the exempt list.

        The Pydantic-annotation tripwire (added post-Codex 2026-05-26) is
        the strongest of the three — a future endpoint can ship a
        ``SomeRequest(scope: str | None)`` body model and forget to read
        ``request.scope`` in the handler; without this check the handler
        would skip the gate AND skip the lint test.
        """
        scope_models = _models_with_scope_field()
        violations: list[str] = []
        for path in SDK_ROUTER_FILES:
            tree = ast.parse(path.read_text())
            handlers = _handler_names_taking_scope(tree)
            for name, node in handlers.items():
                if name in EXEMPT_SDK_HANDLERS:
                    continue
                takes_scope = (
                    _handler_signature_takes_scope(node)
                    or _handler_uses_request_scope(node)
                    or _handler_body_annotation_has_scope(node, scope_models)
                )
                if not takes_scope:
                    continue
                if not _handler_calls_resolver(node):
                    violations.append(
                        f"{path.name}::{name} accepts `scope` but does not call "
                        f"_resolve_sdk_org_id or resolve_effective_scope; "
                        f"add it to EXEMPT_SDK_HANDLERS with a one-line reason "
                        f"if exemption is justified."
                    )

        assert not violations, "Unguarded SDK handlers:\n  " + "\n  ".join(violations)

    def test_scope_model_inventory_is_nonempty(self) -> None:
        """The Pydantic-model walker must find at least one model with
        a ``scope`` field. If it returns empty, either the contract file
        moved or the walker is broken — both cases would silently weaken
        the lint test, so we fail fast.
        """
        models = _models_with_scope_field()
        assert models, (
            "Expected at least one scope-bearing Pydantic model under "
            "api/src/models/contracts/cli.py; got an empty set. The "
            "model-annotation tripwire is silently disabled."
        )

    def test_synthetic_handler_with_scope_model_no_resolver_is_caught(
        self, tmp_path
    ) -> None:
        """Construct a fake router file whose handler accepts a Pydantic
        body annotated with a known scope-bearing model and does NOT call
        the resolver. Run the same AST checks against it and confirm the
        violation is detected. Guards against silent regressions in the
        annotation walker.
        """
        scope_models = _models_with_scope_field()
        assert scope_models, "precondition: contract scope models discoverable"
        # Pick any scope-bearing model name to use in the synthetic handler.
        model_name = next(iter(scope_models))

        synthetic = f"""
from fastapi import APIRouter

router = APIRouter()


@router.post("/probe")
async def synthetic_handler(request: {model_name}):
    # Intentionally does NOT call resolver; should be flagged.
    return {{}}
"""
        tree = ast.parse(synthetic)
        handlers = _handler_names_taking_scope(tree)
        assert "synthetic_handler" in handlers
        node = handlers["synthetic_handler"]
        # Sanity: signature & body-walk checks miss it; annotation check
        # is what surfaces it.
        assert not _handler_signature_takes_scope(node)
        assert not _handler_uses_request_scope(node)
        assert _handler_body_annotation_has_scope(node, scope_models)
        assert not _handler_calls_resolver(node)
