"""
Failing-first proofs for the six external-user leaks found in adversarial
review, plus the repo-method behaviors the hardened lint trusts.

Each test proves the leak is closed: an external, non-bypass principal does
NOT reach the global (organization_id IS NULL) tier through the path under
test, while the sentinel/engine and normal-user paths are unaffected.

LEAK #1 — MCP OAuth token mint drops is_external (auth.py).
LEAK #2 — MCP _get_accessible_agents: cross-org/global role_based leak.
LEAK #3 — MCP get_agent hand-rolls org OR NULL, no external gate.
LEAK #4 — WorkflowRepository.list_tools_for_filter ignores self.is_external.
LEAK #5 — KnowledgeRepository.search/list_* ignore self.is_external.
LEAK #6 — MCP search_knowledge / interactive chat force fallback=True.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.org_filter import OrgFilterType, resolve_org_filter
from src.models import Workflow
from src.models.orm.workflow_roles import WorkflowRole
from src.repositories.data_providers import DataProviderRepository
from src.repositories.knowledge import KnowledgeRepository
from src.repositories.workflows import WorkflowRepository


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    return s


def _compiled(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True}))


def _capture_sql(session) -> str:
    return _compiled(session.execute.await_args.args[0])


def _scalar(value):
    r = MagicMock()
    r.scalar.return_value = value
    r.scalar_one_or_none = MagicMock(return_value=value)
    return r


def _rows(values):
    r = MagicMock()
    r.scalars.return_value.all.return_value = values
    r.all.return_value = values
    return r


# =============================================================================
# resolve_org_filter — central fix for filter-typed leak paths (#4 partial, #5)
# =============================================================================


class TestResolveOrgFilterExternal:
    def _user(self, *, is_external, is_superuser=False, org=...):
        u = MagicMock()
        u.is_superuser = is_superuser
        u.is_external = is_external
        u.organization_id = uuid4() if org is ... else org
        return u

    def test_external_user_gets_org_only(self):
        ft, org = resolve_org_filter(self._user(is_external=True))
        assert ft == OrgFilterType.ORG_ONLY
        assert org is not None

    def test_external_user_ignores_scope(self):
        ft, _ = resolve_org_filter(self._user(is_external=True), scope="global")
        assert ft == OrgFilterType.ORG_ONLY

    def test_normal_user_still_org_plus_global(self):
        ft, _ = resolve_org_filter(self._user(is_external=False))
        assert ft == OrgFilterType.ORG_PLUS_GLOBAL

    def test_external_superuser_unaffected(self):
        # is_superuser short-circuits; external superuser keeps ALL.
        ft, _ = resolve_org_filter(self._user(is_external=True, is_superuser=True))
        assert ft == OrgFilterType.ALL

    def test_external_user_without_org_sees_nothing_global(self):
        # An org-less external must NOT fall into GLOBAL_ONLY (the normal-user
        # edge case). It gets ORG_ONLY with org=None -> resolves to nothing.
        ft, org = resolve_org_filter(self._user(is_external=True, org=None))
        assert ft == OrgFilterType.ORG_ONLY
        assert org is None


# =============================================================================
# LEAK #4 — WorkflowRepository.list_tools_for_filter
# =============================================================================


class TestWorkflowToolsExternal:
    async def test_external_repo_drops_global_arm(self, session):
        session.execute.return_value = _rows([])
        repo = WorkflowRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        await repo.list_tools_for_filter(
            OrgFilterType.ORG_PLUS_GLOBAL, repo.org_id
        )
        sql = _capture_sql(session)
        assert "organization_id IS NULL" not in sql

    async def test_normal_repo_keeps_global_arm(self, session):
        session.execute.return_value = _rows([])
        repo = WorkflowRepository(session, org_id=uuid4(), user_id=uuid4())
        await repo.list_tools_for_filter(
            OrgFilterType.ORG_PLUS_GLOBAL, repo.org_id
        )
        sql = _capture_sql(session)
        assert "organization_id IS NULL" in sql

    async def test_external_repo_global_only_filter_returns_nothing(self, session):
        # An external whose filter resolved to GLOBAL_ONLY (shouldn't happen via
        # resolve_org_filter now, but defense in depth) must not read global.
        session.execute.return_value = _rows([])
        repo = WorkflowRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        await repo.list_tools_for_filter(OrgFilterType.GLOBAL_ONLY, None)
        sql = _capture_sql(session)
        # global-only for an external collapses to a false predicate.
        assert "organization_id IS NULL" not in sql


# =============================================================================
# data_providers.count_active honors external-ness
# =============================================================================


class TestDataProviderCountExternal:
    async def test_external_count_drops_global_arm(self, session):
        session.execute.return_value = _scalar(0)
        repo = DataProviderRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        await repo.count_active()
        sql = _capture_sql(session)
        assert "organization_id IS NULL" not in sql

    async def test_normal_count_keeps_global_arm(self, session):
        session.execute.return_value = _scalar(0)
        repo = DataProviderRepository(session, org_id=uuid4(), user_id=uuid4())
        await repo.count_active()
        sql = _capture_sql(session)
        assert "organization_id IS NULL" in sql


# =============================================================================
# LEAK #5 — KnowledgeRepository.search / list_namespaces / list_documents
# =============================================================================


class TestKnowledgeRepoExternal:
    async def test_external_search_forces_org_only(self, session):
        session.execute.return_value = _rows([])
        repo = KnowledgeRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        await repo.search(
            query_embedding=[0.0] * 8, namespace="ns", fallback=True
        )
        sql = _capture_sql(session)
        assert "organization_id IS NULL" not in sql

    async def test_normal_search_keeps_global_fallback(self, session):
        session.execute.return_value = _rows([])
        repo = KnowledgeRepository(session, org_id=uuid4(), user_id=uuid4())
        await repo.search(
            query_embedding=[0.0] * 8, namespace="ns", fallback=True
        )
        sql = _capture_sql(session)
        assert "organization_id IS NULL" in sql

    async def test_external_list_namespaces_forces_org_only(self, session):
        session.execute.return_value = _rows([])
        repo = KnowledgeRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        await repo.list_namespaces(organization_id=repo.org_id, include_global=True)
        sql = _capture_sql(session)
        assert "organization_id IS NULL" not in sql

    async def test_external_list_documents_forces_org_only(self, session):
        session.execute.return_value = _rows([])
        repo = KnowledgeRepository(
            session, org_id=uuid4(), user_id=uuid4(), is_external=True
        )
        await repo.list_documents_by_namespace(
            namespace="ns", organization_id=repo.org_id, include_global=True
        )
        sql = _capture_sql(session)
        assert "organization_id IS NULL" not in sql
