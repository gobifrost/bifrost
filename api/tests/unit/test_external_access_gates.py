"""
External-user access rules: claim mint + tier-gate tests.

Two things live here:

1. ``resolve_external_claim`` — the token-mint helper that neutralizes the
   raw ``User.is_external`` flag for bypass principals
   (``is_platform_admin OR is_provider_org`` — the canonical C2 rule).
2. The access-level gates that live OUTSIDE ``OrgScopedRepository``: the
   agents-router tool-attach validation and the MCP agent access check.
   The rule: ``authenticated`` ("Everyone except external users") does not
   grant to externals; ``everyone`` does; ``role_based`` grants externals
   exactly what it grants anyone with the role.

``is_external`` deliberately plays NO part in org cascade scoping — the
cascade is pure org→global for every principal (see
api/src/repositories/README.md). Only the access-level check is external-aware.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.external_access import resolve_external_claim

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

