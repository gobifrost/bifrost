"""External SDK callers retain the shared metadata cascade.

Knowledge's direct external restriction is separate from parent-authorized
workflow and Agent access. Table rows remain subject to table policies.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_ROLE_ID
from src.core.auth import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
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
    """The SDK knowledge-search endpoint 403s external principals outright."""

    async def _search(self, user, *, fallback=True):
        session = _session()
        with patch(
            "src.services.embeddings.factory.get_embedding_client",
            AsyncMock(return_value=_embedding_client()),
        ), patch(
            "src.routers.cli._authorize_sdk_knowledge",
            AsyncMock(return_value=(MagicMock(), user.organization_id)),
        ):
            await cli_knowledge_search(
                CLIKnowledgeSearchRequest(query="q", fallback=fallback),
                user,
                session,
            )
        return _executed_sql(session)

    async def test_external_search_is_denied(self):
        session = _session()
        with pytest.raises(HTTPException) as exc:
            await cli_knowledge_search(
                CLIKnowledgeSearchRequest(query="q"),
                _principal(is_external=True),
                session,
            )
        assert exc.value.status_code == 403
        session.execute.assert_not_awaited()

    async def test_normal_user_search_keeps_global_fallback(self):
        sql = await self._search(_principal(is_external=False))
        assert "organization_id IS NULL" in sql

    async def test_internal_search_keeps_global_fallback_with_actor_flag(self):
        sql = await self._search(
            _principal(is_external=False, is_superuser=True)
        )
        assert "organization_id IS NULL" in sql


@pytest.mark.asyncio
class TestCLIListTablesExternal:
    """The SDK tables-list endpoint gives externals the NORMAL user cascade."""

    async def _list(self, user):
        session = _session()
        caller = Caller(user, AuthorizationContext(
            user_id=user.user_id, home_organization_id=user.organization_id,
            base_role_id=USER_ROLE_ID, is_external=user.is_external,
            base_permissions=frozenset({"tables.read"}),
        ))
        with patch("src.services.authorization.enforce.load_caller", AsyncMock(return_value=caller)):
            await cli_list_tables(SDKTableListRequest(), user, session)
        return _executed_sql(session)

    async def test_external_list_gets_normal_cascade(self):
        sql = await self._list(_principal(is_external=True))
        assert "organization_id IS NULL" in sql, (
            "external caller lists org + global table names like any org user"
        )

    async def test_normal_user_list_keeps_global_arm(self):
        sql = await self._list(_principal(is_external=False))
        assert "organization_id IS NULL" in sql

    async def test_actor_flag_does_not_change_the_authorized_cascade(self):
        sql = await self._list(
            _principal(is_external=False, is_superuser=True)
        )
        assert "organization_id IS NULL" in sql
