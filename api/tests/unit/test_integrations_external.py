"""
OPEN-E / OPEN-F failing-first proofs (the fourth-pass HIGH leak — same
secret-leak class as NEW-1, on the /api/sdk/integrations/* sibling endpoints
that nobody gated).

OPEN-E — routers/cli.py sdk_integrations_get / sdk_integrations_refresh_token
are CurrentUser routes (external-reachable). They resolved OAuth tokens and
integration config with an org→GLOBAL cascade never gated on is_external, so
an external portal user with no org mapping fell through to:
  - the GLOBAL OAuth token (decrypted client_secret + access_token), and
  - the GLOBAL integration config defaults (decrypted SECRETs).
The fix mirrors NEW-1 exactly: external → org tier only, NO global fallback.
Engine/sentinel/normal-user path unchanged (still cascades to global).

OPEN-F — services/mcp_server/tools/_http_bridge.py minted a fallback JWT with
no is_external claim, re-opening the global tier to external MCP principals on
the executor/test path.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.repositories.integrations import IntegrationsRepository
from src.repositories.oauth import OAuthTokenRepository


def _session():
    s = AsyncMock()
    s.execute = AsyncMock()
    return s


def _result(rows):
    r = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = rows[0] if rows else None
    scalars.all.return_value = rows
    r.scalars.return_value = scalars
    return r


def _all_sql(session) -> str:
    out = []
    for call in session.execute.await_args_list:
        stmt = call.args[0]
        try:
            out.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        except Exception:
            out.append(str(stmt))
    return "\n".join(out)


# =============================================================================
# OPEN-E: OAuthTokenRepository.get_org_level_for_provider honors external
# =============================================================================


@pytest.mark.asyncio
class TestOAuthTokenExternal:
    async def test_external_repo_accepts_is_external_kwarg(self):
        # The subclass __init__ previously dropped is_external — a TypeError
        # would mean the kwarg never reached the base (silent leak risk).
        repo = OAuthTokenRepository(
            _session(), org_id=uuid4(), is_superuser=False, is_external=True
        )
        assert repo.external_restricted is True

    async def test_external_never_reads_global_token(self):
        session = _session()
        # Org-specific lookup returns nothing → would normally fall to global.
        session.execute.return_value = _result([])
        repo = OAuthTokenRepository(
            session, org_id=uuid4(), is_superuser=False, is_external=True
        )
        token = await repo.get_org_level_for_provider(uuid4())
        assert token is None
        sql = _all_sql(session)
        assert "organization_id IS NULL" not in sql, (
            "external caller must not read the global OAuth token"
        )

    async def test_normal_user_keeps_global_fallback(self):
        session = _session()
        session.execute.return_value = _result([])
        repo = OAuthTokenRepository(session, org_id=uuid4(), is_superuser=True)
        await repo.get_org_level_for_provider(uuid4())
        sql = _all_sql(session)
        assert "organization_id IS NULL" in sql, (
            "sentinel/normal path still cascades to the global token"
        )

    async def test_external_with_no_org_reads_nothing(self):
        session = _session()
        session.execute.return_value = _result([])
        repo = OAuthTokenRepository(
            session, org_id=None, is_superuser=False, is_external=True
        )
        token = await repo.get_org_level_for_provider(uuid4())
        assert token is None
        sql = _all_sql(session)
        assert "organization_id IS NULL" not in sql


# =============================================================================
# OPEN-E: IntegrationsRepository config methods drop the global tier
# =============================================================================


@pytest.mark.asyncio
class TestIntegrationsConfigExternal:
    async def test_config_for_mapping_external_drops_global_arm(self):
        session = _session()
        session.execute.return_value = _result([])
        repo = IntegrationsRepository(session)
        await repo.get_config_for_mapping(uuid4(), uuid4(), external=True)
        sql = _all_sql(session)
        assert "organization_id IS NULL" not in sql, (
            "external mapping config must not include global integration defaults"
        )

    async def test_config_for_mapping_normal_keeps_global_arm(self):
        session = _session()
        session.execute.return_value = _result([])
        repo = IntegrationsRepository(session)
        # external is now a REQUIRED kwarg (NEW-G defense in depth) — the
        # normal/sentinel caller passes False explicitly.
        await repo.get_config_for_mapping(uuid4(), uuid4(), external=False)
        sql = _all_sql(session)
        assert "organization_id IS NULL" in sql

    async def test_integration_defaults_external_returns_empty_no_query(self):
        session = _session()
        repo = IntegrationsRepository(session)
        result = await repo.get_integration_defaults(uuid4(), external=True)
        assert result == {}
        # The defaults ARE the global tier — an external must not even query it.
        session.execute.assert_not_awaited()

    async def test_integration_defaults_normal_queries_global(self):
        session = _session()
        session.execute.return_value = _result([])
        repo = IntegrationsRepository(session)
        await repo.get_integration_defaults(uuid4(), external=False)
        sql = _all_sql(session)
        assert "organization_id IS NULL" in sql


# =============================================================================
# OPEN-F: _http_bridge fallback mint carries is_external
# =============================================================================


class TestHttpBridgeMintCarriesExternal:
    def _claims(self, token: str) -> dict:
        import base64
        import json

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))

    def _ctx(self, *, is_external, is_platform_admin=False):
        return MagicMock(
            user_id=uuid4(),
            user_email="x@y.z",
            user_name="X",
            is_platform_admin=is_platform_admin,
            is_external=is_external,
            org_id=uuid4(),
        )

    def test_external_context_mints_is_external_true(self):
        from src.services.mcp_server.tools._http_bridge import _token_from_context

        token = _token_from_context(self._ctx(is_external=True))
        claims = self._claims(token)
        assert claims.get("is_external") is True, (
            "fallback mint must carry is_external from the MCP context (OPEN-F)"
        )

    def test_non_external_context_mints_is_external_false(self):
        from src.services.mcp_server.tools._http_bridge import _token_from_context

        token = _token_from_context(self._ctx(is_external=False))
        claims = self._claims(token)
        assert claims.get("is_external") is False
