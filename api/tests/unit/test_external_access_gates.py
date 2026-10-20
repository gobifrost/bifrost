"""Provider claim resolution and external access-level gates.

External account flags are preserved at token mint; authenticated resources
exclude external callers unless explicitly shared by role.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from shared.external_access import resolve_provider_org_claim
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


def _user(is_external=True, is_superuser=False, organization_id=...):
    u = MagicMock(spec=["is_external", "is_superuser", "organization_id"])
    u.is_external = is_external
    u.is_superuser = is_superuser
    u.organization_id = uuid4() if organization_id is ... else organization_id
    return u


class TestResolveProviderOrgClaim:
    """``resolve_provider_org_claim`` — the is_provider_org token-mint helper.

    Returns True iff the user's org has ``is_provider`` set. Org-less / system
    users are False (no org row → no provider membership). Uses a single
    indexed organization lookup.
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
# 2a. Agent write policy: validate_user_tool_access
# =============================================================================


def _caller(*, user_id, home, is_external: bool) -> Caller:
    principal = UserPrincipal(
        user_id=user_id,
        email="tool-owner@contoso.example",
        organization_id=home,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=user_id,
            home_organization_id=home,
            base_role_id=uuid4(),
            is_external=is_external,
            base_permissions=frozenset(),
        ),
    )


def _workflow(access_level: str, organization_id):
    wf = MagicMock()
    wf.is_active = True
    wf.access_level = access_level
    wf.name = "wf"
    wf.organization_id = organization_id
    wf.type = "tool"
    return wf


def _tool_repository(monkeypatch, workflow):
    """Install the canonical repository seam used by tool attachment."""
    observed = {}

    class Repository:
        def __init__(self, db, org_id, *, caller):
            observed["db"] = db
            observed["org_id"] = org_id
            observed["caller"] = caller

        async def get(self, *, id):
            observed["id"] = id
            return workflow

    monkeypatch.setattr("src.services.agent_write_policy.WorkflowRepository", Repository)
    return observed


class TestValidateUserToolAccessExternal:
    async def test_authenticated_workflow_denied_when_canonical_policy_refuses(
        self, monkeypatch
    ):
        from src.services.agent_write_policy import validate_user_tool_access

        tool_id = str(uuid4())
        home = uuid4()
        caller = _caller(user_id=uuid4(), home=home, is_external=True)
        db = AsyncMock()
        _tool_repository(monkeypatch, None)
        with pytest.raises(HTTPException) as exc:
            await validate_user_tool_access(db, caller, [tool_id])
        assert exc.value.status_code == 403

    async def test_authenticated_workflow_uses_canonical_policy_for_regular_user(
        self, monkeypatch
    ):
        from src.services.agent_write_policy import validate_user_tool_access

        tool_id = str(uuid4())
        home = uuid4()
        caller = _caller(user_id=uuid4(), home=home, is_external=False)
        db = AsyncMock()
        observed = _tool_repository(monkeypatch, _workflow("authenticated", home))
        await validate_user_tool_access(db, caller, [tool_id])
        assert observed["id"] == UUID(tool_id)
        assert observed["caller"].ctx is caller.ctx
        assert observed["caller"].principal.user_id == caller.principal.user_id
        assert observed["caller"].principal.runtime_authority is None

    async def test_role_based_workflow_allowed_when_canonical_policy_allows(
        self, monkeypatch
    ):
        from src.services.agent_write_policy import validate_user_tool_access

        tool_id = str(uuid4())
        home = uuid4()
        caller = _caller(user_id=uuid4(), home=home, is_external=True)
        db = AsyncMock()
        _tool_repository(monkeypatch, _workflow("role_based", home))
        await validate_user_tool_access(db, caller, [tool_id])

    async def test_foreign_tool_is_refused_by_canonical_repository(self, monkeypatch):
        from src.services.agent_write_policy import validate_user_tool_access

        tool_id = str(uuid4())
        caller = _caller(user_id=uuid4(), home=uuid4(), is_external=False)
        db = AsyncMock()
        _tool_repository(monkeypatch, None)

        with pytest.raises(HTTPException) as exc:
            await validate_user_tool_access(db, caller, [tool_id])

        assert exc.value.status_code == 403

    async def test_runtime_powers_are_not_used_for_agent_tool_configuration(
        self, monkeypatch
    ):
        from src.services.agent_write_policy import validate_user_tool_access

        tool_id = str(uuid4())
        home = uuid4()
        caller = _caller(user_id=uuid4(), home=home, is_external=False)
        caller.principal.runtime_authority = MagicMock()
        observed = _tool_repository(monkeypatch, _workflow("authenticated", home))

        await validate_user_tool_access(AsyncMock(), caller, [tool_id])

        assert observed["caller"].principal.runtime_authority is None

    @pytest.mark.parametrize(
        ("active", "workflow_type", "expected"),
        [
            (False, "tool", "inactive"),
            (True, "workflow", "not a tool"),
        ],
    )
    async def test_resolved_tool_must_be_active_and_have_tool_type(
        self, monkeypatch, active, workflow_type, expected
    ):
        from src.services.agent_write_policy import validate_user_tool_access

        home = uuid4()
        workflow = _workflow("authenticated", home)
        workflow.is_active = active
        workflow.type = workflow_type
        _tool_repository(monkeypatch, workflow)

        with pytest.raises(HTTPException, match=expected) as exc:
            await validate_user_tool_access(
                AsyncMock(),
                _caller(user_id=uuid4(), home=home, is_external=False),
                [str(uuid4())],
            )

        assert exc.value.status_code == 422
