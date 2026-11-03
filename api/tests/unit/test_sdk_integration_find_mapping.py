"""Unit tests for the additive exact integration-mapping lookup (#1016)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from shared.sdk_integrations import find_sdk_integration_mapping_dict
from src.core.principal import UserPrincipal
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller


def _ctx(
    *grants: RoleGrant, home: UUID | None, base: frozenset[str] = USER_BASE_PERMISSIONS
) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=base,
        role_grants=grants,
    )


def _person(ctx: AuthorizationContext) -> Caller:
    principal = UserPrincipal(
        user_id=ctx.user_id, email="find@example.test", organization_id=ctx.home_organization_id
    )
    return Caller(principal, ctx)


def _read_at(boundary: Boundary) -> RoleGrant:
    return RoleGrant(uuid4(), frozenset({"integrations.read"}), (boundary,))


@pytest.fixture
async def find_world(db_session):
    org_a = Organization(name=f"find-a-{uuid4().hex[:8]}", is_active=True, created_by="t")
    org_b = Organization(name=f"find-b-{uuid4().hex[:8]}", is_active=True, created_by="t")
    integration = Integration(name=f"find-{uuid4().hex[:8]}")
    db_session.add_all([org_a, org_b, integration])
    await db_session.flush()
    db_session.add_all(
        [
            IntegrationMapping(
                integration_id=integration.id, organization_id=org_a.id, entity_id="ent-a"
            ),
            IntegrationMapping(
                integration_id=integration.id, organization_id=org_b.id, entity_id="ent-b"
            ),
        ]
    )
    await db_session.flush()
    return {"a": org_a.id, "b": org_b.id, "name": integration.name}


@pytest.mark.asyncio
class TestFindMapping:
    async def test_list_exact_agreement(self, db_session, find_world) -> None:
        from shared.sdk_integrations import list_sdk_integration_mappings

        caller = _person(
            _ctx(_read_at(Boundary(BoundaryKind.ORGANIZATION, find_world["b"])), home=find_world["a"])
        )
        items = await list_sdk_integration_mappings(
            db_session, name=find_world["name"], caller=caller
        )
        assert items is not None and len(items) == 1

        found = await find_sdk_integration_mapping_dict(
            db_session, name=find_world["name"], entity_id="ent-b", caller=caller
        )
        assert found is not None
        assert found["status"] == "unique"
        assert found["mapping"] is not None
        assert found["mapping"]["entity_id"] == "ent-b"
        assert found["mapping"]["id"] == items[0]["id"]
        assert found["receipt"]["searched"] == "authorized_reach"
        assert found["receipt"]["operation"] == "integrations.mappings.list"
        assert found["receipt"]["entity_id"] == "ent-b"
        # Receipt never claims global absence.
        assert "global" not in str(found["receipt"]).lower() or "authorized" in str(
            found["receipt"]
        ).lower()
        assert found["status"] != "not_found"

    async def test_duplicate_entity_ids_are_ambiguous(self, db_session) -> None:
        org_a = Organization(name=f"dup-a-{uuid4().hex[:8]}", is_active=True, created_by="t")
        org_b = Organization(name=f"dup-b-{uuid4().hex[:8]}", is_active=True, created_by="t")
        integration = Integration(name=f"dup-{uuid4().hex[:8]}")
        db_session.add_all([org_a, org_b, integration])
        await db_session.flush()
        for oid in (org_a.id, org_b.id):
            db_session.add(
                IntegrationMapping(
                    integration_id=integration.id, organization_id=oid, entity_id="shared-ent"
                )
            )
        await db_session.flush()
        caller = _person(
            _ctx(
                _read_at(Boundary(BoundaryKind.ORGANIZATION, org_a.id)),
                _read_at(Boundary(BoundaryKind.ORGANIZATION, org_b.id)),
                home=org_a.id,
            )
        )
        with patch(
            "shared.sdk_integrations._mapping_item_dict", new=AsyncMock()
        ) as item_mock:
            found = await find_sdk_integration_mapping_dict(
                db_session, name=integration.name, entity_id="shared-ent", caller=caller
            )
        assert found is not None
        assert found["status"] == "ambiguous"
        assert found["mapping"] is None
        # Zero tenant actions on unresolved: no per-mapping hydration.
        item_mock.assert_not_awaited()

    async def test_inaccessible_entity_is_not_found_not_forbidden(
        self, db_session, find_world
    ) -> None:
        caller = _person(
            _ctx(
                _read_at(Boundary(BoundaryKind.ORGANIZATION, find_world["a"])),
                home=find_world["a"],
            )
        )
        with patch(
            "shared.sdk_integrations._mapping_item_dict", new=AsyncMock()
        ) as item_mock:
            found = await find_sdk_integration_mapping_dict(
                db_session, name=find_world["name"], entity_id="ent-b", caller=caller
            )
        # Reachable caller, unreachable entity: scoped miss, not 403.
        assert found is not None
        assert found["status"] == "not_found_in_authorized_scope"
        assert found["mapping"] is None
        item_mock.assert_not_awaited()

    async def test_missing_integration_returns_none(self, db_session, find_world) -> None:
        caller = _person(
            _ctx(
                _read_at(Boundary(BoundaryKind.ORGANIZATION, find_world["a"])),
                home=find_world["a"],
            )
        )
        assert (
            await find_sdk_integration_mapping_dict(
                db_session, name="missing-nope", entity_id="ent-a", caller=caller
            )
            is None
        )

    async def test_no_reach_still_refused(self, db_session, find_world) -> None:
        caller = _person(_ctx(home=find_world["a"]))
        with pytest.raises(HTTPException) as exc_info:
            await find_sdk_integration_mapping_dict(
                db_session, name=find_world["name"], entity_id="ent-a", caller=caller
            )
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
class TestFindMappingFacade:
    async def test_facade_sends_name_and_entity_id(self) -> None:
        from bifrost.integrations import integrations

        item = {
            "id": "mid", "integration_id": "iid", "organization_id": "oid",
            "entity_id": "ent-1", "entity_name": None, "oauth_token_id": None,
            "config": {}, "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        payload = {
            "status": "unique",
            "mapping": item,
            "receipt": {
                "operation": "integrations.mappings.list",
                "searched": "authorized_reach",
                "candidates_considered": 1,
                "entity_id": "ent-1",
            },
        }
        client = AsyncMock()
        client.engine_request = AsyncMock(
            return_value=httpx.Response(
                200, json=payload,
                request=httpx.Request("POST", "http://api/api/sdk/integrations/find_mapping"),
            )
        )
        with patch("bifrost.integrations.get_client", return_value=client):
            result = await integrations.find_mapping("P", entity_id="ent-1")
        assert result is not None
        assert result.status == "unique"
        assert result.mapping is not None and result.mapping.entity_id == "ent-1"
        client.engine_request.assert_awaited_once_with(
            "POST",
            "/api/sdk/integrations/find_mapping",
            json={"name": "P", "entity_id": "ent-1"},
            retry_transient=True,
        )

    async def test_facade_missing_integration_returns_none(self) -> None:
        from bifrost.integrations import integrations

        client = AsyncMock()
        client.engine_request = AsyncMock(
            return_value=httpx.Response(
                200, content=b"null", headers={"content-type": "application/json"},
                request=httpx.Request("POST", "http://api/api/sdk/integrations/find_mapping"),
            )
        )
        with patch("bifrost.integrations.get_client", return_value=client):
            assert await integrations.find_mapping("Missing", entity_id="x") is None
