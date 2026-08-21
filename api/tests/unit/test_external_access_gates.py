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

from shared.external_access import (
    resolve_external_claim,
    resolve_provider_org_claim,
)
from src.core.principal import UserPrincipal
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationContext,
)

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


class TestResolveProviderOrgClaim:
    """``resolve_provider_org_claim`` — the is_provider_org token-mint helper.

    Returns True iff the user's org has ``is_provider`` set. Org-less / system
    users are False (no org row → no provider membership). Mirrors the single
    indexed SELECT of ``resolve_external_claim``.
    """

    async def test_provider_org_member_is_true(self):
        db = AsyncMock()
        db.scalar.return_value = True  # org.is_provider
        assert await resolve_provider_org_claim(db, _user()) is True

    async def test_regular_org_member_is_false(self):
        db = AsyncMock()
        db.scalar.return_value = False
        assert await resolve_provider_org_claim(db, _user()) is False

    async def test_orgless_user_is_false_without_lookup(self):
        db = AsyncMock()
        assert (
            await resolve_provider_org_claim(db, _user(organization_id=None))
            is False
        )
        db.scalar.assert_not_awaited()

    async def test_none_provider_flag_coerces_to_false(self):
        # A NULL is_provider column (no org row matched) must be a hard False.
        db = AsyncMock()
        db.scalar.return_value = None
        assert await resolve_provider_org_claim(db, _user()) is False


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


def _authorization(*, user_id, is_external: bool = False) -> AuthorizationContext:
    organization_id = uuid4()
    user = UserPrincipal(
        user_id=user_id,
        email="user@example.test",
        organization_id=organization_id,
        is_superuser=False,
        is_external=is_external,
    )
    return AuthorizationContext(
        requester=user,
        effective_actor=user,
        selected_boundary=AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset({"agents.readwrite"}),
        grant_sources=(),
    )


class _WorkflowRepoStub:
    instances: list["_WorkflowRepoStub"] = []
    next_workflow = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.instances.append(self)

    async def get(self, **kwargs):
        self.get_kwargs = kwargs
        return self.next_workflow


class TestValidateUserToolAccessExternal:
    async def test_authenticated_workflow_denied_for_external_without_role(
        self, monkeypatch
    ):
        from src.routers.agents import _validate_user_tool_access
        import src.routers.agents as agents_router

        tool_id = str(uuid4())
        db = AsyncMock()
        _WorkflowRepoStub.instances = []
        _WorkflowRepoStub.next_workflow = None
        monkeypatch.setattr(agents_router, "WorkflowRepository", _WorkflowRepoStub)

        with pytest.raises(HTTPException) as exc:
            await _validate_user_tool_access(
                db, _authorization(user_id=uuid4(), is_external=True), [tool_id]
            )
        assert exc.value.status_code == 403
        assert _WorkflowRepoStub.instances[0].kwargs["is_external"] is True

    async def test_authenticated_workflow_allowed_for_regular_user(self, monkeypatch):
        from src.routers.agents import _validate_user_tool_access
        import src.routers.agents as agents_router

        tool_id = str(uuid4())
        db = AsyncMock()
        _WorkflowRepoStub.instances = []
        _WorkflowRepoStub.next_workflow = _workflow("authenticated")
        monkeypatch.setattr(agents_router, "WorkflowRepository", _WorkflowRepoStub)

        await _validate_user_tool_access(
            db, _authorization(user_id=uuid4()), [tool_id]
        )
        assert _WorkflowRepoStub.instances[0].kwargs["is_external"] is False

    async def test_role_based_workflow_allowed_for_external_with_role(self, monkeypatch):
        from src.routers.agents import _validate_user_tool_access
        import src.routers.agents as agents_router

        tool_id = str(uuid4())
        db = AsyncMock()
        _WorkflowRepoStub.instances = []
        _WorkflowRepoStub.next_workflow = _workflow("role_based")
        monkeypatch.setattr(agents_router, "WorkflowRepository", _WorkflowRepoStub)

        await _validate_user_tool_access(
            db, _authorization(user_id=uuid4(), is_external=True), [tool_id]
        )
        assert _WorkflowRepoStub.instances[0].kwargs["is_external"] is True


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
