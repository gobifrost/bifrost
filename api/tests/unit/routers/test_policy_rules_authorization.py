"""Boundary-aware authorization tests for policy rule routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate
from src.services.authorization import AuthorizationBoundary
from src.routers import policy_rules as policy_rules_mod


def _principal(*, organization_id: UUID | None) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="policy@example.com",
        organization_id=organization_id,
    )


class _FakeAuthorization:
    def __init__(
        self,
        *,
        boundary: AuthorizationBoundary,
        requester: UserPrincipal,
    ) -> None:
        self.selected_boundary = boundary
        self.requester = requester
        self.operations: list[str] = []
        self.resource_boundaries: list[UUID | None] = []

    def require_operation(self, operation_id: str) -> None:
        self.operations.append(operation_id)

    def require_resource_boundary(self, organization_id: UUID | None) -> None:
        self.resource_boundaries.append(organization_id)


class _FakeResult:
    def __init__(self, values: list[UUID]) -> None:
        self._values = values

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[UUID]:
        return self._values


class _FakeDb:
    def __init__(
        self,
        *,
        managed_org_ids: list[UUID] | None = None,
        provider_flags: dict[UUID, bool] | None = None,
    ) -> None:
        self.managed_org_ids = managed_org_ids or []
        self.provider_flags = provider_flags or {}
        self.commit_calls = 0

    async def execute(self, _stmt):  # noqa: ANN001
        return _FakeResult(self.managed_org_ids)

    async def scalar(self, _stmt):  # noqa: ANN001
        # The helper only uses this for provider validation.
        if not self.provider_flags:
            return False
        return next(iter(self.provider_flags.values()))

    async def commit(self) -> None:
        self.commit_calls += 1


def _rule_row(*, organization_id: UUID | None, name: str = "rule") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        name=name,
        domain="file",
        description="desc",
        body={"actions": ["read"], "when": None},
        is_builtin=False,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_resolve_policy_rule_org_scope_honors_selected_boundary() -> None:
    org_id = uuid4()
    requester = _principal(organization_id=org_id)
    auth = _FakeAuthorization(
        boundary=AuthorizationBoundary.organization(org_id),
        requester=requester,
    )
    ctx = SimpleNamespace(db=_FakeDb())

    scope, managed = await policy_rules_mod._resolve_policy_rule_org_scope(
        ctx,
        auth,
        None,
        allow_managed_collection=False,
    )

    assert scope == org_id
    assert managed is False

    auth.selected_boundary = AuthorizationBoundary.platform()
    with pytest.raises(HTTPException, match="global policy rules"):
        await policy_rules_mod._resolve_policy_rule_org_scope(
            ctx,
            auth,
            org_id,
            allow_managed_collection=False,
        )

    managed_org_id = uuid4()
    auth.selected_boundary = AuthorizationBoundary.managed_organizations()
    ctx.db = _FakeDb(provider_flags={managed_org_id: False})
    with pytest.raises(HTTPException, match="Select one organization"):
        await policy_rules_mod._resolve_policy_rule_org_scope(
            ctx,
            auth,
            managed_org_id,
            allow_managed_collection=False,
        )

    scope, managed = await policy_rules_mod._resolve_policy_rule_org_scope(
        ctx,
        auth,
        managed_org_id,
        allow_managed_collection=True,
    )
    assert scope == managed_org_id
    assert managed is False

@pytest.mark.asyncio
async def test_create_update_and_delete_use_requester_attribution_and_exact_boundary(
    monkeypatch,
) -> None:
    org_id = uuid4()
    requester = _principal(organization_id=org_id)
    auth = _FakeAuthorization(
        boundary=AuthorizationBoundary.organization(org_id),
        requester=requester,
    )
    ctx = SimpleNamespace(db=_FakeDb())
    row = _rule_row(organization_id=org_id, name="policy")

    calls: list[tuple[str, UUID | None, str]] = []

    class _FakeService:
        def __init__(self, db) -> None:  # noqa: ANN001
            self.db = db

        async def create(self, body, *, actor):  # noqa: ANN001
            calls.append(("create", body.organization_id, actor.email))
            return row

        async def get(self, name, domain, *, org_id):  # noqa: ANN001
            calls.append(("get", org_id, name))
            return row

        async def update(self, name, domain, body, *, org_id, actor):  # noqa: ANN001
            calls.append(("update", org_id, actor.email))
            return row

        async def delete(self, name, domain, *, org_id, actor):  # noqa: ANN001
            calls.append(("delete", org_id, actor.email))

        async def usages(self, name, domain, *, org_id):  # noqa: ANN001
            return SimpleNamespace(file_policies=[], tables=[], total=0)

    monkeypatch.setattr(policy_rules_mod, "PolicyRuleService", _FakeService)

    created = await policy_rules_mod.create_policy_rule(
        PolicyRuleCreate(
            name="policy",
            domain="file",
            description="desc",
            body={"actions": ["read"], "when": None},
            organization_id=org_id,
        ),
        ctx,
        auth,
    )
    assert created.organization_id == org_id

    updated = await policy_rules_mod.update_policy_rule(
        "file",
        "policy",
        PolicyRuleUpdate(description="updated"),
        ctx,
        auth,
        organization_id=None,
    )
    assert updated.name == "policy"

    await policy_rules_mod.delete_policy_rule(
        "file",
        "policy",
        ctx,
        auth,
        organization_id=None,
    )

    assert auth.operations == [
        "policy.rules.create",
        "policy.rules.update",
        "policy.rules.delete",
    ]
    assert auth.resource_boundaries == [org_id, org_id, org_id]
    assert calls == [
        ("create", org_id, requester.email),
        ("get", org_id, "policy"),
        ("update", org_id, requester.email),
        ("get", org_id, "policy"),
        ("delete", org_id, requester.email),
    ]
    assert ctx.db.commit_calls == 3


@pytest.mark.asyncio
async def test_list_policy_rules_scopes_to_selected_boundary_and_rejects_widening(
    monkeypatch,
) -> None:
    managed_org_1 = uuid4()
    managed_org_2 = uuid4()
    requester = _principal(organization_id=managed_org_1)

    class _FakeRepo:
        calls: list[UUID | None] = []

        def __init__(
            self,
            session,
            org_id,
            bypass_resource_admission,
        ):  # noqa: ANN001
            self.session = session
            self.org_id = org_id
            self.bypass_resource_admission = bypass_resource_admission
            _FakeRepo.calls.append(org_id)

        async def list(self, **filters):  # noqa: ANN001
            domain = filters.get("domain", "file")
            return [
                _rule_row(organization_id=self.org_id, name=f"{self.org_id.hex[:8]}-{domain}")
            ]

    class _ListDb(_FakeDb):
        async def execute(self, _stmt):  # noqa: ANN001
            return _FakeResult([managed_org_1, managed_org_2])

    _FakeRepo.calls = []
    monkeypatch.setattr(policy_rules_mod, "PolicyRuleRepository", _FakeRepo)
    ctx = SimpleNamespace(db=_ListDb())

    auth = _FakeAuthorization(
        boundary=AuthorizationBoundary.organization(managed_org_1),
        requester=requester,
    )
    rows = await policy_rules_mod.list_policy_rules(
        ctx,
        auth,
        domain="file",
        organization_id=None,
    )
    assert rows[0].organization_id == managed_org_1
    assert auth.operations[-1] == "policy.rules.list"

    auth.selected_boundary = AuthorizationBoundary.platform()
    with pytest.raises(HTTPException, match="global policy rules"):
        await policy_rules_mod.list_policy_rules(
            ctx,
            auth,
            domain="file",
            organization_id=managed_org_1,
        )

    auth.selected_boundary = AuthorizationBoundary.managed_organizations()
    rows = await policy_rules_mod.list_policy_rules(
        ctx,
        auth,
        domain="file",
        organization_id=None,
    )
    assert {row.organization_id for row in rows} == {managed_org_1, managed_org_2}
    assert _FakeRepo.calls == [managed_org_1, managed_org_1, managed_org_2]
