"""Shared SDK table metadata service (``shared.sdk_table_metadata``).

The HTTP handlers (``POST /api/sdk/tables/create``, ``POST
/api/sdk/tables/list``, ``DELETE /api/tables/{table_id}``) delegate to
this service. These tests pin the service contract and the thin-adapter
parity:

- exact-scope duplicate vs global name cascade on create,
- Solution execution-context rejection on create,
- org / provider / external scope handling,
- seeded ``admin_bypass`` policy and ``created_by`` attribution,
- sorted org+global cascade on list (incl. external sentinel trust),
- Solution-managed and absent deletes,
- HTTP adapter status/detail parity.
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from shared.policies.probe import make_seed_admin_bypass
from shared.sdk_table_metadata import (
    SDKTableMetadataError,
    create_sdk_table,
    delete_sdk_table,
    list_sdk_tables,
)
from src.core.auth import UserPrincipal
from src.models.contracts.cli import (
    SDKTableCreateRequest,
    SDKTableInfo,
    SDKTableListRequest,
)
from src.models.orm.tables import Table
from src.services.solutions.guard import SOLUTION_MANAGED_MESSAGE


async def _seed_org(db_session, *, is_provider=False):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-table-meta-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-table-meta-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_solution(db_session, *, org_id=None):
    from src.models.orm.solutions import Solution as SolutionModel

    row = SolutionModel(
        id=uuid4(),
        slug=f"sdk-table-meta-{uuid4().hex[:8]}",
        name="sdk table meta test",
        organization_id=org_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_table(db_session, name, *, org_id=None, solution_id=None):
    row = Table(
        name=name,
        organization_id=org_id,
        solution_id=solution_id,
        created_by="sdk-table-meta-test",
        access=make_seed_admin_bypass(),
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _row_by_name(db_session, name, *, org_id=None):
    result = await db_session.execute(
        select(Table).where(Table.name == name, Table.organization_id == org_id)
    )
    return result.scalar_one_or_none()


def _user(*, org_id=None, is_superuser=False, is_external=False):
    return UserPrincipal(
        user_id=uuid4(),
        email="sdk-table-meta@test.local",
        organization_id=org_id,
        is_superuser=is_superuser,
        is_external=is_external,
    )


def _ctx(db_session, user, *, org_id=None, solution_id=None):
    return SimpleNamespace(
        db=db_session,
        org_id=org_id,
        solution_id=solution_id,
        app_id=None,
        user=user,
    )


def _table_name(tag):
    return f"t{tag}_{uuid4().hex[:6]}"


@pytest.mark.asyncio
class TestCreateService:
    async def test_create_persists_row_with_attribution_and_seed(self, db_session):
        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        name = _table_name(tag)

        result = await create_sdk_table(
            db_session,
            name=name,
            table_schema={"columns": [{"name": "label"}]},
            description="meta test",
            org_id=org.id,
            actor_email="writer@test.local",
            solution_present=False,
        )

        assert result["name"] == name
        assert result["organization_id"] == str(org.id)
        assert result["table_schema"] == {"columns": [{"name": "label"}]}
        assert result["description"] == "meta test"
        assert result["created_at"] and result["updated_at"]

        row = await _row_by_name(db_session, name, org_id=org.id)
        assert row is not None
        assert row.created_by == "writer@test.local"
        assert row.solution_id is None
        assert row.access == make_seed_admin_bypass()

    async def test_create_duplicate_exact_scope_is_409(self, db_session):
        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        name = _table_name(tag)
        await _seed_table(db_session, name, org_id=org.id)

        with pytest.raises(SDKTableMetadataError) as exc:
            await create_sdk_table(
                db_session,
                name=name,
                table_schema=None,
                description=None,
                org_id=org.id,
                actor_email="writer@test.local",
                solution_present=False,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == f"Table '{name}' already exists"

    async def test_create_same_name_other_org_allowed(self, db_session):
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        name = _table_name(tag)
        await _seed_table(db_session, name, org_id=org_a.id)

        result = await create_sdk_table(
            db_session,
            name=name,
            table_schema=None,
            description=None,
            org_id=org_b.id,
            actor_email="writer@test.local",
            solution_present=False,
        )
        assert result["organization_id"] == str(org_b.id)

    async def test_create_org_name_ignores_global_cascade(self, db_session):
        """Exact scope, not cascade: a global row must not block an org create."""
        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        name = _table_name(tag)
        await _seed_table(db_session, name, org_id=None)

        result = await create_sdk_table(
            db_session,
            name=name,
            table_schema=None,
            description=None,
            org_id=org.id,
            actor_email="writer@test.local",
            solution_present=False,
        )
        assert result["organization_id"] == str(org.id)

    async def test_create_global_name_ignores_org_cascade(self, db_session):
        """Exact scope, not cascade: an org row must not block a global create."""
        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        name = _table_name(tag)
        await _seed_table(db_session, name, org_id=org.id)

        result = await create_sdk_table(
            db_session,
            name=name,
            table_schema=None,
            description=None,
            org_id=None,
            actor_email="writer@test.local",
            solution_present=False,
        )
        assert result["organization_id"] is None

    async def test_create_ignores_solution_managed_name_collision(self, db_session):
        """The duplicate check is _repo-namespace only (solution rows coexist)."""
        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org_id=org.id)
        tag = uuid4().hex[:8]
        name = _table_name(tag)
        await _seed_table(db_session, name, org_id=org.id, solution_id=sol.id)

        result = await create_sdk_table(
            db_session,
            name=name,
            table_schema=None,
            description=None,
            org_id=org.id,
            actor_email="writer@test.local",
            solution_present=False,
        )
        assert result["name"] == name

    async def test_create_solution_context_rejected(self, db_session):
        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]

        with pytest.raises(SDKTableMetadataError) as exc:
            await create_sdk_table(
                db_session,
                name=_table_name(tag),
                table_schema=None,
                description=None,
                org_id=org.id,
                actor_email="writer@test.local",
                solution_present=True,
            )
        assert exc.value.status_code == 404
        assert exc.value.detail == "Tables must be declared by the solution manifest"


@pytest.mark.asyncio
class TestCreateHandler:
    async def test_handler_duplicate_maps_to_409(self, db_session):
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        name = _table_name(tag)
        await _seed_table(db_session, name, org_id=org.id)
        user = _user(org_id=org.id)

        with pytest.raises(HTTPException) as exc:
            await cli_create_table(
                SDKTableCreateRequest(name=name, scope=str(org.id)),
                _ctx(db_session, user, org_id=org.id),
                user,
                db_session,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == f"Table '{name}' already exists"

    async def test_handler_solution_context_maps_to_404(self, db_session):
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org_id=org.id)
        user = _user(org_id=org.id, is_superuser=True)
        tag = uuid4().hex[:8]
        name = _table_name(tag)

        with pytest.raises(HTTPException) as exc:
            await cli_create_table(
                SDKTableCreateRequest(name=name, scope=str(org.id)),
                _ctx(db_session, user, org_id=org.id, solution_id=str(sol.id)),
                user,
                db_session,
            )
        assert exc.value.status_code == 404
        assert await _row_by_name(db_session, name, org_id=org.id) is None

    async def test_handler_malformed_scope_is_422(self, db_session):
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        user = _user(org_id=org.id)
        tag = uuid4().hex[:8]

        with pytest.raises(HTTPException) as exc:
            await cli_create_table(
                SDKTableCreateRequest(name=_table_name(tag), scope="not-a-uuid"),
                _ctx(db_session, user, org_id=org.id),
                user,
                db_session,
            )
        assert exc.value.status_code == 422

    async def test_solution_restriction_precedes_malformed_scope(self, db_session):
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org_id=org.id)
        user = _user(org_id=org.id)

        with pytest.raises(HTTPException) as exc:
            await cli_create_table(
                SDKTableCreateRequest(name=_table_name("precedence"), scope="not-a-uuid"),
                _ctx(db_session, user, org_id=org.id, solution_id=str(sol.id)),
                user,
                db_session,
            )
        assert exc.value.status_code == 404
        assert exc.value.detail == "Tables must be declared by the solution manifest"

    async def test_handler_cross_org_scope_denied(self, db_session):
        from src.routers.cli import cli_create_table

        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        user = _user(org_id=org_a.id)
        tag = uuid4().hex[:8]

        with pytest.raises(HTTPException) as exc:
            await cli_create_table(
                SDKTableCreateRequest(name=_table_name(tag), scope=str(org_b.id)),
                _ctx(db_session, user, org_id=org_a.id),
                user,
                db_session,
            )
        assert exc.value.status_code == 403

    async def test_handler_provider_cross_org_allowed(self, db_session):
        from src.routers.cli import cli_create_table

        provider_org = await _seed_org(db_session, is_provider=True)
        other_org = await _seed_org(db_session)
        user = _user(org_id=provider_org.id)
        tag = uuid4().hex[:8]
        name = _table_name(tag)

        result = await cli_create_table(
            SDKTableCreateRequest(name=name, scope=str(other_org.id)),
            _ctx(db_session, user, org_id=provider_org.id),
            user,
            db_session,
        )
        assert result.name == name
        assert result.organization_id == str(other_org.id)

    async def test_handler_global_create_by_admin(self, db_session):
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        user = _user(org_id=org.id, is_superuser=True)
        tag = uuid4().hex[:8]
        name = _table_name(tag)

        result = await cli_create_table(
            SDKTableCreateRequest(name=name, scope="global"),
            _ctx(db_session, user, org_id=org.id),
            user,
            db_session,
        )
        assert result.organization_id is None

    async def test_handler_adapter_parity(self, db_session):
        """The handler passes authoritative scalars through and wraps the result."""
        import shared.sdk_table_metadata as svc
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        user = _user(org_id=org.id)
        tag = uuid4().hex[:8]
        name = _table_name(tag)
        real_create = svc.create_sdk_table

        with patch.object(
            svc, "create_sdk_table", wraps=real_create
        ) as create_spy:
            result = await cli_create_table(
                SDKTableCreateRequest(name=name, scope=str(org.id)),
                _ctx(db_session, user, org_id=org.id),
                user,
                db_session,
            )

        create_spy.assert_awaited_once()
        _, kwargs = create_spy.call_args
        assert kwargs == {
            "name": name,
            "table_schema": None,
            "description": None,
            "org_id": org.id,
            "actor_email": user.email,
            "solution_present": False,
        }
        row = await _row_by_name(db_session, name, org_id=org.id)
        assert row is not None
        assert result == SDKTableInfo(
            id=str(row.id),
            name=row.name,
            organization_id=str(row.organization_id),
            table_schema=row.schema,
            description=row.description,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )


@pytest.mark.asyncio
class TestListService:
    async def test_list_sorted_org_plus_global_cascade(self, db_session):
        org = await _seed_org(db_session)
        other_org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        org_names = sorted([f"tz{tag}", f"ta{tag}", f"tm{tag}"])
        global_name = f"tg{tag}"
        foreign_name = f"tf{tag}"
        for name in reversed(org_names):
            await _seed_table(db_session, name, org_id=org.id)
        await _seed_table(db_session, global_name, org_id=None)
        await _seed_table(db_session, foreign_name, org_id=other_org.id)

        items = await list_sdk_tables(
            db_session, org_id=org.id, external=False
        )

        # Other tests in this run commit global rows to the shared DB, so
        # scope assertions to this test's tag.
        mine = [item["name"] for item in items if tag in item["name"]]
        assert mine == sorted([*org_names, global_name])
        assert foreign_name not in [item["name"] for item in items]
        assert all(set(item) == set(SDKTableInfo.model_fields) for item in items)

    async def test_list_global_scope_only_global(self, db_session):
        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        global_name = f"tg{tag}"
        org_name = f"to{tag}"
        await _seed_table(db_session, global_name, org_id=None)
        await _seed_table(db_session, org_name, org_id=org.id)

        items = await list_sdk_tables(db_session, org_id=None, external=False)

        assert global_name in [item["name"] for item in items]
        assert org_name not in [item["name"] for item in items]

    async def test_list_external_keeps_cascade_drops_sentinel(self, db_session):
        """OPEN-B: externals list org+global names but without superuser trust."""
        import shared.sdk_table_metadata as svc

        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        org_name = f"te{tag}"
        global_name = f"tx{tag}"
        await _seed_table(db_session, org_name, org_id=org.id)
        await _seed_table(db_session, global_name, org_id=None)

        captured = {}
        real_repo = svc.tables_repo_module.TableRepository

        class SpyRepo(real_repo):  # type: ignore[valid-type, misc]
            def __init__(self, session, org_id, **kwargs):
                captured.update(org_id=org_id, **kwargs)
                super().__init__(session, org_id, **kwargs)

        with patch.object(svc.tables_repo_module, "TableRepository", SpyRepo):
            items = await list_sdk_tables(
                db_session, org_id=org.id, external=True
            )

        assert captured["is_superuser"] is False
        assert captured["is_external"] is True
        assert captured["org_id"] == org.id
        mine = sorted(item["name"] for item in items if tag in item["name"])
        assert mine == sorted([org_name, global_name])

    async def test_list_engine_trust(self, db_session):
        """Non-external callers keep the sentinel is_superuser=True trust."""
        import shared.sdk_table_metadata as svc

        org = await _seed_org(db_session)
        captured = {}
        real_repo = svc.tables_repo_module.TableRepository

        class SpyRepo(real_repo):  # type: ignore[valid-type, misc]
            def __init__(self, session, org_id, **kwargs):
                captured.update(org_id=org_id, **kwargs)
                super().__init__(session, org_id, **kwargs)

        with patch.object(svc.tables_repo_module, "TableRepository", SpyRepo):
            await list_sdk_tables(db_session, org_id=org.id, external=False)

        assert captured["is_superuser"] is True
        assert captured["is_external"] is False


@pytest.mark.asyncio
class TestListHandler:
    async def test_handler_returns_sorted_dtos(self, db_session):
        from src.routers.cli import cli_list_tables

        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        names = sorted([f"lz{tag}", f"la{tag}"])
        for name in reversed(names):
            await _seed_table(db_session, name, org_id=org.id)
        user = _user(org_id=org.id)

        service_items = await list_sdk_tables(
            db_session, org_id=org.id, external=False
        )
        handler_items = await cli_list_tables(
            SDKTableListRequest(scope=str(org.id)), user, db_session
        )

        assert [item.name for item in handler_items if tag in item.name] == names
        assert handler_items == [SDKTableInfo(**item) for item in service_items]

    async def test_handler_external_lists_cascade(self, db_session):
        from src.routers.cli import cli_list_tables

        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        org_name = f"le{tag}"
        global_name = f"lg{tag}"
        await _seed_table(db_session, org_name, org_id=org.id)
        await _seed_table(db_session, global_name, org_id=None)
        user = _user(org_id=org.id, is_external=True)

        items = await cli_list_tables(
            SDKTableListRequest(scope=None), user, db_session
        )

        mine = sorted(item.name for item in items if tag in item.name)
        assert mine == sorted([org_name, global_name])


@pytest.mark.asyncio
class TestDeleteService:
    async def test_delete_removes_row(self, db_session):
        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        row = await _seed_table(db_session, _table_name(tag), org_id=org.id)

        assert (
            await delete_sdk_table(
                db_session, table_id=row.id, org_id=org.id
            )
        ) is True
        assert (
            await db_session.execute(select(Table).where(Table.id == row.id))
        ).scalar_one_or_none() is None

    async def test_delete_absent_returns_false(self, db_session):
        org = await _seed_org(db_session)

        assert (
            await delete_sdk_table(
                db_session, table_id=uuid4(), org_id=org.id
            )
        ) is False

    async def test_delete_solution_managed_is_409(self, db_session):
        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org_id=org.id)
        tag = uuid4().hex[:8]
        row = await _seed_table(
            db_session, _table_name(tag), org_id=org.id, solution_id=sol.id
        )

        with pytest.raises(SDKTableMetadataError) as exc:
            await delete_sdk_table(
                db_session, table_id=row.id, org_id=org.id
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == SOLUTION_MANAGED_MESSAGE


@pytest.mark.asyncio
class TestDeleteHandler:
    async def test_handler_absent_maps_to_404(self, db_session):
        from src.routers.tables import delete_table

        org = await _seed_org(db_session)
        user = _user(org_id=org.id, is_superuser=True)
        missing = uuid4()

        with pytest.raises(HTTPException) as exc:
            await delete_table(missing, _ctx(db_session, user, org_id=org.id), user)
        assert exc.value.status_code == 404
        assert exc.value.detail == f"Table '{missing}' not found"

    async def test_handler_solution_managed_maps_to_409(self, db_session):
        from src.routers.tables import delete_table

        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org_id=org.id)
        tag = uuid4().hex[:8]
        row = await _seed_table(
            db_session, _table_name(tag), org_id=org.id, solution_id=sol.id
        )
        user = _user(org_id=org.id, is_superuser=True)

        with pytest.raises(HTTPException) as exc:
            await delete_table(
                row.id, _ctx(db_session, user, org_id=org.id), user
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == SOLUTION_MANAGED_MESSAGE

    async def test_handler_adapter_parity(self, db_session):
        """The handler passes table_id/org_id through and maps False to 404."""
        import shared.sdk_table_metadata as svc
        from src.routers.tables import delete_table

        org = await _seed_org(db_session)
        user = _user(org_id=org.id, is_superuser=True)
        tag = uuid4().hex[:8]
        row = await _seed_table(db_session, _table_name(tag), org_id=org.id)
        real_delete = svc.delete_sdk_table

        with patch.object(
            svc, "delete_sdk_table", wraps=real_delete
        ) as delete_spy:
            assert (
                await delete_table(
                    row.id, _ctx(db_session, user, org_id=org.id), user
                )
            ) is None

        delete_spy.assert_awaited_once()
        _, kwargs = delete_spy.call_args
        assert kwargs == {"table_id": row.id, "org_id": org.id}
