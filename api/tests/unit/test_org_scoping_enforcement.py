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
    ('routers/config.py', 'query = query.where(self.model.organization_id.is_(None))', 'ConfigRepository cascade override; phase 5 absorbs into base'),
    ('routers/config.py', 'query = query.where(self.model.organization_id == self.org_id)', 'ConfigRepository cascade override; phase 5 absorbs into base'),
    ('routers/config.py', 'self.model.organization_id == self.org_id,', 'ConfigRepository cascade override; phase 5 absorbs into base'),
    ('routers/config.py', 'self.model.organization_id.is_(None),', 'ConfigRepository cascade override; phase 5 absorbs into base'),
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
    ('routers/knowledge_sources.py', 'stmt = stmt.where(KnowledgeStore.organization_id.is_(None))', 'knowledge sources inline cascade; phase 6 migrates'),
    ('routers/knowledge_sources.py', 'stmt = stmt.where(KnowledgeStore.organization_id == filter_org_id)', 'knowledge sources inline cascade; phase 6 migrates'),
    ('routers/knowledge_sources.py', 'KnowledgeStore.organization_id == filter_org_id,', 'knowledge sources inline cascade; phase 6 migrates'),
    ('routers/knowledge_sources.py', 'KnowledgeStore.organization_id.is_(None),', 'knowledge sources inline cascade; phase 6 migrates'),
    ('routers/knowledge_sources.py', 'KnowledgeStore.organization_id == target_org_id,', 'knowledge sources inline cascade; phase 6 migrates'),
    ('routers/llm_config.py', 'SystemConfig.organization_id.is_(None),', 'SystemConfig admin lookup; pre-repo pattern (permanent)'),
    ('routers/mcp_connections.py', 'query = query.where(MCPConnection.organization_id == scope_org)', 'MCP connection org filter; phase 6 migrates'),
    ('routers/metrics.py', 'query = query.where(ExecutionMetricsDaily.organization_id == org_uuid)', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    ('routers/metrics.py', 'query = query.where(ExecutionMetricsDaily.organization_id.is_(None))', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    ('routers/metrics.py', '.join(Organization, ExecutionMetricsDaily.organization_id == Organization.id)', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    ('routers/metrics.py', '.where(ExecutionMetricsDaily.organization_id.is_(None))', 'ExecutionMetricsDaily identity-entity filter (permanent)'),
    ('routers/oauth_connections.py', 'OAuthProvider.organization_id == org_id,', 'OAuthProvider/Token inline; phase 6 (UI surface) migrates'),
    ('routers/oauth_connections.py', 'OAuthProvider.organization_id.is_(None),', 'OAuthProvider/Token inline; phase 6 (UI surface) migrates'),
    ('routers/oauth_connections.py', 'query = query.where(OAuthProvider.organization_id.is_(None))', 'OAuthProvider/Token inline; phase 6 (UI surface) migrates'),
    ('routers/oauth_connections.py', 'query = query.where(OAuthToken.organization_id == org_id)', 'OAuthProvider/Token inline; phase 6 (UI surface) migrates'),
    ('routers/oauth_connections.py', 'query = query.where(OAuthToken.organization_id.is_(None))', 'OAuthProvider/Token inline; phase 6 (UI surface) migrates'),
    ('routers/oauth_connections.py', 'SystemConfig.organization_id.is_(None),  # Global system config', 'SystemConfig admin lookup; pre-repo pattern (permanent)'),
    ('routers/oauth_connections.py', 'SystemConfig.organization_id.is_(None),', 'SystemConfig admin lookup; pre-repo pattern (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(ExecutionMetricsDaily.organization_id.is_(None))', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(ExecutionMetricsDaily.organization_id == org_uuid)', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(WorkflowROIDaily.organization_id.is_(None))', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', 'query = query.where(WorkflowROIDaily.organization_id == org_uuid)', 'identity-entity scope filter (permanent)'),
    ('routers/roi_reports.py', '.join(Organization, ExecutionMetricsDaily.organization_id == Organization.id)', 'identity-entity scope filter (permanent)'),
    ('routers/roles.py', 'KnowledgeNamespaceRoleORM.organization_id == entry.organization_id,', 'KnowledgeNamespaceRole identity-entity filter (permanent)'),
    ('routers/tables.py', 'query = query.where(self.model.organization_id.is_(None))', 'TableRepository cascade override; phase 6 relocates'),
    ('routers/tables.py', 'query = query.where(self.model.organization_id == self.org_id)', 'TableRepository cascade override; phase 6 relocates'),
    ('routers/tables.py', 'self.model.organization_id == self.org_id,', 'TableRepository cascade override; phase 6 relocates'),
    ('routers/tables.py', 'CustomClaimORM.organization_id == organization_id', 'tables custom claim cross-ref; phase 6 migrates'),
    ('routers/tools.py', 'query = query.where(Workflow.organization_id == filter_org_id)', 'tools workflow list; phase 6 migrates'),
    ('routers/tools.py', 'query = query.where(Workflow.organization_id.is_(None))', 'tools workflow list; phase 6 migrates'),
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
    ('routers/websocket.py', '| (TableOrm.organization_id.is_(None))', 'websocket table subscription filter; phase 6 migrates'),
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
    # When phase 7 renames /api/cli/* to /api/sdk/*, update this list.
    ROUTERS_DIR / "cli.py",
}


# Endpoints under /api/cli/* that do NOT touch execution-resolution entities
# and therefore don't need to call resolve_effective_scope. The path keys
# below are used to identify them via the @router decorator.
EXEMPT_SDK_ENDPOINTS: dict[str, str] = {
    # Auth and session
    "/auth/login": "auth — caller identity IS the input",
    "/auth/refresh": "auth — token refresh, no scope",
    "/auth/whoami": "auth — current user lookup",
    "/context": "developer context — does its own platform-admin gate",
    # Health, version, capabilities
    "/version": "health/version, no org data",
    "/download": "CLI binary download, no org data",
    # CLI session lifecycle
    "/sessions/register": "CLI session bootstrap, no org data",
    "/sessions/state": "CLI session state lookup",
    "/sessions/continue": "CLI session continuation",
    "/sessions/pending": "CLI session work pickup",
    "/sessions/log": "CLI session log write",
    "/sessions/result": "CLI session result write",
}


class TestSDKEndpointsUseResolver:
    """SDK endpoints that take a `scope` parameter must call
    resolve_effective_scope (or be on the exempt list).

    This test is the **placeholder skeleton** — the migration phases (4-7)
    convert endpoints onto resolve_effective_scope and shrink the exempt
    list. Today, the function does not exist as a caller yet, so this
    test only verifies the infrastructure: the file exists, the test can
    parse it, and the exempt list is well-formed.

    A future iteration of this test (post-phase-1 migration) will assert
    that every non-exempt endpoint with a `scope` field actually invokes
    `resolve_effective_scope`. That's intentionally NOT done now because:

    1. No endpoints call the resolver yet — phase 4 introduces the first
       caller.
    2. Adding the strict check before any callers exist would require
       allow-listing every existing scope-taking endpoint, defeating the
       purpose.

    What this test enforces today:
      - The resolver module exists and can be imported.
      - The exempt list is well-formed.

    What it will enforce after phase 4:
      - Each non-exempt SDK endpoint accepting scope calls resolve_effective_scope.
    """

    def test_resolver_module_importable(self) -> None:
        from shared.scope_resolver import resolve_effective_scope

        assert callable(resolve_effective_scope)

    def test_exempt_list_well_formed(self) -> None:
        for path, reason in EXEMPT_SDK_ENDPOINTS.items():
            assert path.startswith("/"), f"Exempt endpoint path must be absolute: {path}"
            assert reason, f"Exempt endpoint {path} needs a one-line justification"

    def test_sdk_router_files_exist(self) -> None:
        for path in SDK_ROUTER_FILES:
            assert path.exists(), f"SDK router file missing: {path}"
