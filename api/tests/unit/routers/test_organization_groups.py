"""Router tests for provider-owned organization groups."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models import Organization, OrganizationGroupCreate, OrganizationGroupUpdate
from src.routers import organization_groups as router


def _organization(*, is_provider: bool) -> Organization:
    return Organization(
        id=uuid4(),
        name="Org",
        domain=None,
        is_active=True,
        is_provider=is_provider,
        settings={},
        created_by="admin@example.com",
    )


def _db_with_organization(owner: Organization, members: list[Organization]) -> MagicMock:
    db = MagicMock()
    db.get = AsyncMock(return_value=owner)
    result = MagicMock()
    result.scalars.return_value.all.return_value = members
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_create_organization_group_persists_members_and_audits(monkeypatch):
    owner = _organization(is_provider=True)
    member_a = _organization(is_provider=False)
    member_b = _organization(is_provider=False)
    db = _db_with_organization(owner, [member_a, member_b])
    user = MagicMock(user_id=uuid4())
    authorize = AsyncMock()
    audit = AsyncMock()

    monkeypatch.setattr(router, "authorize_capability", authorize)
    monkeypatch.setattr(router, "emit_audit", audit)
    monkeypatch.setattr(
        router,
        "_provider_organization_id",
        AsyncMock(return_value=owner.id),
    )
    monkeypatch.setattr(
        router,
        "_public",
        lambda group: {
            "name": group.name,
            "owner_organization_id": group.owner_organization_id,
            "member_organization_ids": [m.organization_id for m in group.memberships],
        },
    )

    request = OrganizationGroupCreate(
        name="Managed Pods",
        member_organization_ids=[member_a.id, member_b.id, member_a.id],
    )

    group = await router.create_organization_group(request=request, user=user, db=db)
    payload = cast(dict[str, Any], group)

    assert payload["name"] == "Managed Pods"
    assert payload["owner_organization_id"] == owner.id
    assert payload["member_organization_ids"] == [member_a.id, member_b.id]
    assert authorize.await_count == 1
    assert authorize.await_args.kwargs["capability"] == "organizationgroups.readwrite"
    assert db.add.call_count == 1
    assert db.flush.await_count == 1
    assert audit.await_count == 1


@pytest.mark.asyncio
async def test_validated_organizations_rejects_non_provider_owner():
    owner = _organization(is_provider=False)
    db = _db_with_organization(owner, [])

    with pytest.raises(HTTPException, match="must be owned by the provider organization"):
        await router._validated_organizations(
            db,
            owner_organization_id=owner.id,
            member_organization_ids=[],
        )


@pytest.mark.asyncio
async def test_update_organization_group_replaces_members(monkeypatch):
    owner = _organization(is_provider=True)
    group = MagicMock()
    group.id = uuid4()
    group.owner_organization_id = owner.id
    group.name = "Managed Pods"
    group.created_at = group.updated_at = None
    group.memberships = []
    member = _organization(is_provider=False)

    db = MagicMock()
    db.get = AsyncMock(return_value=owner)
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [member]
    db.execute = AsyncMock(return_value=execute_result)
    db.scalar = AsyncMock(return_value=group)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    authorize = AsyncMock()
    audit = AsyncMock()
    monkeypatch.setattr(router, "authorize_capability", authorize)
    monkeypatch.setattr(router, "emit_audit", audit)
    invalidate = AsyncMock()
    monkeypatch.setattr(router, "_invalidate_group_assignees", invalidate)
    monkeypatch.setattr(
        router,
        "_public",
        lambda grp: {
            "name": grp.name,
            "member_organization_ids": [m.organization_id for m in grp.memberships],
        },
    )

    request = OrganizationGroupUpdate(
        member_organization_ids=[member.id],
    )

    updated = await router.update_organization_group(
        group_id=group.id,
        request=request,
        user=MagicMock(user_id=uuid4()),
        db=db,
    )
    payload = cast(dict[str, Any], updated)

    assert payload["member_organization_ids"] == [member.id]
    assert db.flush.await_count == 1
    assert audit.await_count == 1
    invalidate.assert_awaited_once_with(db, group.id)


@pytest.mark.asyncio
async def test_provider_organization_id_requires_one_provider():
    provider_id = uuid4()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [provider_id]
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    assert await router._provider_organization_id(db) == provider_id

    result.scalars.return_value.all.return_value = []
    with pytest.raises(HTTPException, match="Exactly one provider organization"):
        await router._provider_organization_id(db)


@pytest.mark.asyncio
async def test_delete_group_removes_now_empty_assignments_and_invalidates(monkeypatch):
    group_id = uuid4()
    user_id = uuid4()
    boundary = MagicMock(organization_group_id=group_id)
    assignment = MagicMock(user_id=user_id, boundaries=[boundary])

    assignments_result = MagicMock()
    assignments_result.scalars.return_value.unique.return_value.all.return_value = [
        assignment
    ]
    delete_result = MagicMock(rowcount=1)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[assignments_result, delete_result])
    db.delete = AsyncMock()
    db.flush = AsyncMock()

    authorize = AsyncMock()
    audit = AsyncMock()
    invalidate = AsyncMock()
    monkeypatch.setattr(router, "_authorize", authorize)
    monkeypatch.setattr(router, "emit_audit", audit)
    monkeypatch.setattr(router, "invalidate_user", invalidate)

    await router.delete_organization_group(
        group_id=group_id,
        user=MagicMock(user_id=uuid4()),
        db=db,
    )

    db.delete.assert_awaited_once_with(assignment)
    db.flush.assert_awaited_once()
    invalidate.assert_awaited_once_with(user_id)
    audit.assert_awaited_once()
