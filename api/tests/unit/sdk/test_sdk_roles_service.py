"""Service unit tests + router thin-boundary tests for role operations.

The business behavior behind the nine ``api/bifrost/roles.py`` SDK
methods (``create``, ``get``, ``list``, ``update``, ``delete``,
``list_users``, ``list_forms``, ``assign_users``, ``assign_forms``)
lives in ``shared.sdk_roles``; the HTTP handlers in
``api/src/routers/roles.py`` are thin delegates. These tests pin:

- the service against a real DB: success paths, 404 precedence,
  assignment transaction behavior (email fallback, unknown-skip,
  idempotent re-assign), and the historical quirks (no 404 on empty
  assignment listings, ``ValueError`` on a malformed form id, no
  consumer counts on update);
- the router boundary with the service mocked: delegation arguments,
  ``RoleServiceError`` -> ``HTTPException`` mapping, the
  ``X-Total-Count`` header, and that guard 409s propagate unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from shared.sdk_roles import RoleServiceError


def _stub_user(email: str = "admin@test.local"):
    return SimpleNamespace(email=email)


async def _seed_org(db_session):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-roles-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="sdk-roles-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, email: str | None = None, name: str = "Seed User"):
    from src.models import User as UserORM

    org = await _seed_org(db_session)
    row = UserORM(
        email=email or f"seed-{uuid4().hex[:8]}@test.local",
        name=name,
        organization_id=org.id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_form(db_session, *, name: str | None = None):
    from src.models import Form as FormORM

    row = FormORM(
        name=name or f"seed-form-{uuid4().hex[:8]}",
        created_by="seed@test.local",
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.mark.asyncio
class TestRoleService:
    async def test_create_get_round_trip(self, db_session):
        from shared.sdk_roles import create_role, get_role

        created = await create_role(
            db_session,
            name="Tech",
            description="Technicians",
            permissions=None,
            actor_email="admin@test.local",
        )
        assert created.name == "Tech"
        assert created.description == "Technicians"
        assert created.permissions == {}
        assert created.created_by == "admin@test.local"
        assert created.consumer_counts is not None
        assert created.consumer_counts.users == 0

        fetched = await get_role(db_session, role_id=created.id)
        assert fetched.id == created.id
        assert fetched.name == "Tech"

    async def test_create_with_permissions(self, db_session):
        from shared.sdk_roles import create_role

        created = await create_role(
            db_session,
            name="Perm Role",
            description=None,
            permissions={"tickets": ["read"]},
            actor_email="admin@test.local",
        )
        assert created.permissions == {"tickets": ["read"]}

    async def test_get_missing_raises_404(self, db_session):
        from shared.sdk_roles import get_role

        with pytest.raises(RoleServiceError) as exc_info:
            await get_role(db_session, role_id=uuid4())
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Role not found"

    async def test_list_search_sort_pagination(self, db_session):
        from shared.sdk_roles import create_role, list_roles

        tag = uuid4().hex[:8]
        await create_role(
            db_session, name=f"Zulu {tag}", description=None,
            permissions=None, actor_email="a@t.local",
        )
        await create_role(
            db_session, name=f"Alpha {tag}", description="findme",
            permissions=None, actor_email="a@t.local",
        )

        items, total = await list_roles(db_session, search=tag)
        assert total == 2
        assert [r.name for r in items] == [f"Alpha {tag}", f"Zulu {tag}"]

        items, total = await list_roles(
            db_session, search="findme", limit=1, offset=0
        )
        assert total == 1
        assert items[0].name == f"Alpha {tag}"

        items, total = await list_roles(
            db_session, search=tag, limit=1, offset=1
        )
        assert total == 2
        assert [r.name for r in items] == [f"Zulu {tag}"]

    async def test_update_partial_and_404(self, db_session):
        from shared.sdk_roles import create_role, update_role

        created = await create_role(
            db_session, name="Before", description="d",
            permissions=None, actor_email="a@t.local",
        )
        updated = await update_role(
            db_session, role_id=created.id, description="after",
            actor_email="a@t.local",
        )
        assert updated.name == "Before"
        assert updated.description == "after"
        # Historical quirk: single-role update carries no consumer counts.
        assert updated.consumer_counts is None

        with pytest.raises(RoleServiceError) as exc_info:
            await update_role(
                db_session, role_id=uuid4(), name="x", actor_email="a@t.local"
            )
        assert exc_info.value.status_code == 404

    async def test_delete_removes_and_404s(self, db_session):
        from shared.sdk_roles import create_role, delete_role, get_role

        created = await create_role(
            db_session, name="Gone", description=None,
            permissions=None, actor_email="a@t.local",
        )
        assert await delete_role(db_session, role_id=created.id) == "Gone"
        with pytest.raises(RoleServiceError) as exc_info:
            await get_role(db_session, role_id=created.id)
        assert exc_info.value.status_code == 404

        with pytest.raises(RoleServiceError) as exc_info:
            await delete_role(db_session, role_id=uuid4())
        assert exc_info.value.status_code == 404

    async def test_assign_users_id_uuid_email_and_unknown(self, db_session):
        from shared.sdk_roles import (
            assign_users_to_role,
            create_role,
            list_role_users,
        )

        role = await create_role(
            db_session, name="Crew", description=None,
            permissions=None, actor_email="a@t.local",
        )
        u1 = await _seed_user(db_session)
        u2 = await _seed_user(db_session)

        await assign_users_to_role(
            db_session,
            role_id=role.id,
            user_ids=[str(u1.id), u2.email, "nobody@test.local"],
            actor_email="a@t.local",
        )
        listed = await list_role_users(db_session, role_id=role.id)
        assert sorted(listed.user_ids) == sorted([str(u1.id), str(u2.id)])
        assert listed.total == 2

        # Idempotent re-assign: no duplicates.
        await assign_users_to_role(
            db_session,
            role_id=role.id,
            user_ids=[str(u1.id), u2.email],
            actor_email="a@t.local",
        )
        listed = await list_role_users(db_session, role_id=role.id)
        assert listed.total == 2

    async def test_list_users_unknown_role_is_empty_not_404(self, db_session):
        from shared.sdk_roles import list_role_users

        listed = await list_role_users(db_session, role_id=uuid4())
        assert listed.user_ids == []
        assert listed.total == 0

    async def test_assign_forms_success_and_idempotent(self, db_session):
        from shared.sdk_roles import (
            assign_forms_to_role,
            create_role,
            list_role_forms,
        )

        role = await create_role(
            db_session, name="Forms", description=None,
            permissions=None, actor_email="a@t.local",
        )
        form = await _seed_form(db_session)

        await assign_forms_to_role(
            db_session, role_id=role.id, form_ids=[str(form.id)],
            actor_email="a@t.local",
        )
        assert (await list_role_forms(db_session, role_id=role.id)).form_ids == [
            str(form.id)
        ]

        await assign_forms_to_role(
            db_session, role_id=role.id, form_ids=[str(form.id)],
            actor_email="a@t.local",
        )
        assert (await list_role_forms(db_session, role_id=role.id)).form_ids == [
            str(form.id)
        ]

    async def test_assign_forms_missing_form_404(self, db_session):
        from shared.sdk_roles import assign_forms_to_role, create_role

        role = await create_role(
            db_session, name="Forms404", description=None,
            permissions=None, actor_email="a@t.local",
        )
        missing = str(uuid4())
        with pytest.raises(RoleServiceError) as exc_info:
            await assign_forms_to_role(
                db_session, role_id=role.id, form_ids=[missing],
                actor_email="a@t.local",
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == f"Form with ID '{missing}' not found"

    async def test_assign_forms_malformed_id_raises_value_error(self, db_session):
        """Historical behavior: a non-UUID form id raises ValueError."""
        from shared.sdk_roles import assign_forms_to_role, create_role

        role = await create_role(
            db_session, name="FormsBad", description=None,
            permissions=None, actor_email="a@t.local",
        )
        with pytest.raises(ValueError):
            await assign_forms_to_role(
                db_session, role_id=role.id, form_ids=["not-a-uuid"],
                actor_email="a@t.local",
            )

    async def test_list_forms_unknown_role_is_empty_not_404(self, db_session):
        from shared.sdk_roles import list_role_forms

        assert (await list_role_forms(db_session, role_id=uuid4())).form_ids == []


@pytest.mark.asyncio
class TestRolesRouterBoundary:
    """Handlers delegate to ``shared.sdk_roles`` and map its errors."""

    async def test_list_sets_total_header(self):
        from src.models import RolePublic
        from src.routers.roles import list_roles

        role_id = uuid4()
        public = RolePublic(
            id=role_id, name="R", description=None, permissions={},
            created_by="a@t.local", created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        response = Response()
        with patch(
            "shared.sdk_roles.list_roles",
            new=AsyncMock(return_value=([public], 7)),
        ) as mock_list:
            items = await list_roles(
                _stub_user(), AsyncMock(), response,
                search="R", sort_by="name", sort_direction="asc",
                limit=None, offset=0,
            )
        assert items == [public]
        assert response.headers["X-Total-Count"] == "7"
        mock_list.assert_awaited_once()
        assert mock_list.call_args[1] == {
            "search": "R", "sort_by": "name", "sort_direction": "asc",
            "limit": None, "offset": 0,
        }

    async def test_create_delegates_and_maps_404(self):
        from src.models import RoleCreate
        from src.routers.roles import create_role

        request = RoleCreate(name="N", description="D", permissions={"a": 1})
        with patch(
            "shared.sdk_roles.create_role", new=AsyncMock(return_value="ROLE")
        ) as mock_create:
            result = await create_role(request, _stub_user("me@t.local"), AsyncMock())
        assert result == "ROLE"
        kwargs = mock_create.call_args[1]
        assert kwargs == {
            "name": "N", "description": "D", "permissions": {"a": 1},
            "actor_email": "me@t.local",
        }

        with patch(
            "shared.sdk_roles.create_role",
            new=AsyncMock(side_effect=RoleServiceError(404, "Role not found")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_role(request, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Role not found"

    async def test_get_maps_404(self):
        from src.routers.roles import get_role

        role_id = uuid4()
        with patch(
            "shared.sdk_roles.get_role", new=AsyncMock(return_value="ROLE")
        ) as mock_get:
            assert await get_role(role_id, _stub_user(), AsyncMock()) == "ROLE"
        mock_get.assert_awaited_once()
        assert mock_get.call_args[1] == {"role_id": role_id}

        with patch(
            "shared.sdk_roles.get_role",
            new=AsyncMock(side_effect=RoleServiceError(404, "Role not found")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_role(role_id, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 404

    async def test_update_delegates_partial_fields(self):
        from src.models import RoleUpdate
        from src.routers.roles import update_role

        role_id = uuid4()
        request = RoleUpdate(description="new")
        with patch(
            "shared.sdk_roles.update_role", new=AsyncMock(return_value="ROLE")
        ) as mock_update:
            assert await update_role(role_id, request, _stub_user(), AsyncMock()) == "ROLE"
        kwargs = mock_update.call_args[1]
        assert kwargs["role_id"] == role_id
        assert kwargs["name"] is None
        assert kwargs["description"] == "new"
        assert kwargs["permissions"] is None

    async def test_delete_maps_404_but_lets_guard_409_through(self):
        from src.routers.roles import delete_role

        role_id = uuid4()
        with patch(
            "shared.sdk_roles.delete_role", new=AsyncMock(return_value="N")
        ):
            assert await delete_role(role_id, _stub_user(), AsyncMock()) is None

        with patch(
            "shared.sdk_roles.delete_role",
            new=AsyncMock(side_effect=RoleServiceError(404, "Role not found")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_role(role_id, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 404

        guard_409 = HTTPException(status_code=409, detail="locked")
        with patch(
            "shared.sdk_roles.delete_role",
            new=AsyncMock(side_effect=guard_409),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_role(role_id, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 409

    async def test_assign_users_delegates(self):
        from src.models import AssignUsersToRoleRequest
        from src.routers.roles import assign_users_to_role

        role_id = uuid4()
        request = AssignUsersToRoleRequest(user_ids=["u1"])
        with patch(
            "shared.sdk_roles.assign_users_to_role", new=AsyncMock()
        ) as mock_assign:
            assert (
                await assign_users_to_role(role_id, request, _stub_user("m@t.local"), AsyncMock())
                is None
            )
        kwargs = mock_assign.call_args[1]
        assert kwargs == {
            "role_id": role_id, "user_ids": ["u1"], "actor_email": "m@t.local"
        }

    async def test_list_users_delegates(self):
        from src.routers.roles import get_role_users

        role_id = uuid4()
        with patch(
            "shared.sdk_roles.list_role_users", new=AsyncMock(return_value="RESP")
        ) as mock_list:
            assert await get_role_users(
                role_id, _stub_user(), AsyncMock(),
                search="s", limit=10, offset=5,
            ) == "RESP"
        kwargs = mock_list.call_args[1]
        assert kwargs == {
            "role_id": role_id, "search": "s", "limit": 10, "offset": 5
        }

    async def test_assign_forms_delegates_and_maps_404(self):
        from src.models import AssignFormsToRoleRequest
        from src.routers.roles import assign_forms_to_role

        role_id = uuid4()
        request = AssignFormsToRoleRequest(form_ids=[str(uuid4())])
        with patch(
            "shared.sdk_roles.assign_forms_to_role", new=AsyncMock()
        ) as mock_assign:
            assert (
                await assign_forms_to_role(role_id, request, _stub_user(), AsyncMock())
                is None
            )
        assert mock_assign.call_args[1]["role_id"] == role_id

        with patch(
            "shared.sdk_roles.assign_forms_to_role",
            new=AsyncMock(side_effect=RoleServiceError(404, "missing")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await assign_forms_to_role(role_id, request, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 404

    async def test_list_forms_delegates(self):
        from src.routers.roles import get_role_forms

        role_id = uuid4()
        with patch(
            "shared.sdk_roles.list_role_forms", new=AsyncMock(return_value="RESP")
        ) as mock_list:
            assert await get_role_forms(role_id, _stub_user(), AsyncMock()) == "RESP"
        mock_list.assert_awaited_once()
        assert mock_list.call_args[1] == {"role_id": role_id}
