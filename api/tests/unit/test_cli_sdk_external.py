"""
OPEN-A / OPEN-B failing-first proofs: /api/sdk REST endpoints in
``src/routers/cli.py`` hardcoded ``is_superuser=True`` (sentinel trust) on a
PLAIN ``CurrentUser`` route, so a direct EXTERNAL caller inherited the full
cascade:

OPEN-A — POST /api/sdk/knowledge/search: an external portal user read GLOBAL
         knowledge-store DOCUMENT CONTENT (the request's ``fallback=True``
         default was honored because ``external_restricted`` never engaged).
OPEN-B — POST /api/sdk/tables/list: an external user listed GLOBAL table
         names/schemas (the cascade union included the NULL-org tier).

The fix constructs the repository from the calling PRINCIPAL: the engine
sentinel and admins keep sentinel trust (their ``is_external`` claim is
bypass-neutralized at token mint, so ``not is_external`` stays True), while
an external principal gets ``is_superuser=False, is_external=True`` →
``external_restricted`` engages (org tier only, fallback forced off).
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.auth import UserPrincipal
from src.models.contracts.cli import CLIKnowledgeSearchRequest, SDKTableListRequest
from src.routers.cli import cli_knowledge_search, cli_list_tables


def _principal(*, is_external: bool, is_superuser: bool = False, org_id=...):
    return UserPrincipal(
        user_id=uuid4(),
        email="x@y.z",
        organization_id=uuid4() if org_id is ... else org_id,
        is_superuser=is_superuser,
        is_external=is_external,
    )


def _session(rows=()):
    s = AsyncMock()
    result = MagicMock()
    result.all.return_value = list(rows)
    result.scalars.return_value.all.return_value = list(rows)
    result.scalar_one_or_none = MagicMock(return_value=None)
    s.execute = AsyncMock(return_value=result)
    return s


def _executed_sql(session) -> str:
    out = []
    for call in session.execute.await_args_list:
        stmt = call.args[0]
        try:
            out.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        except Exception:
            out.append(str(stmt))
    return "\n".join(out)


def _embedding_client():
    client = MagicMock()
    client.embed_single = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return client


@pytest.mark.asyncio
class TestCLIKnowledgeSearchExternal:
    """OPEN-A: the SDK knowledge-search endpoint must drop the global tier
    for an external principal even though the request defaults fallback=True."""

    async def _search(self, user, *, fallback=True):
        session = _session()
        with patch(
            "src.services.embeddings.get_embedding_client",
            AsyncMock(return_value=_embedding_client()),
        ):
            await cli_knowledge_search(
                CLIKnowledgeSearchRequest(query="q", fallback=fallback),
                user,
                session,
            )
        return _executed_sql(session)

    async def test_external_search_drops_global_arm(self):
        sql = await self._search(_principal(is_external=True))
        assert "organization_id IS NULL" not in sql, (
            "external caller must not reach global knowledge content"
        )

    async def test_normal_user_search_keeps_global_fallback(self):
        sql = await self._search(_principal(is_external=False))
        assert "organization_id IS NULL" in sql

    async def test_sentinel_search_unchanged(self):
        # The engine sentinel (superuser, is_external=False at mint) keeps the
        # full cascade — workflow runtime resolution is intentionally NOT
        # external-restricted (a workflow is an API endpoint to its caller).
        sql = await self._search(
            _principal(is_external=False, is_superuser=True)
        )
        assert "organization_id IS NULL" in sql


@pytest.mark.asyncio
class TestCLIListTablesExternal:
    """OPEN-B: the SDK tables-list endpoint must return the org tier only
    for an external principal (no global table names/schemas)."""

    async def _list(self, user):
        session = _session()
        await cli_list_tables(SDKTableListRequest(), user, session)
        return _executed_sql(session)

    async def test_external_list_drops_global_arm(self):
        sql = await self._list(_principal(is_external=True))
        assert "organization_id IS NULL" not in sql, (
            "external caller must not list global tables"
        )

    async def test_normal_user_list_keeps_global_arm(self):
        sql = await self._list(_principal(is_external=False))
        assert "organization_id IS NULL" in sql

    async def test_sentinel_list_unchanged(self):
        sql = await self._list(
            _principal(is_external=False, is_superuser=True)
        )
        assert "organization_id IS NULL" in sql
