"""Service unit tests + router thin-boundary tests for organization operations.

The business behavior behind the five ``api/bifrost/organizations.py`` SDK
methods (``create``, ``get``, ``list``, ``update``, ``delete``) lives in
``shared.sdk_organizations``; the HTTP handlers in
``api/src/routers/organizations.py`` are thin delegates. These tests pin:

- the service against a real DB: success paths, 404/403 error precedence,
  provider-org protections, list ordering/filter defaults, domain
  lowercasing, and soft-delete behavior;
- the router boundary with the service mocked: delegation arguments and
  ``OrganizationServiceError`` -> ``HTTPException`` mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.sdk_organizations import OrganizationServiceError


def _stub_user(email: str = "admin@test.local"):
    return SimpleNamespace(email=email)


async def _make_provider(db_session, org_id):
    from sqlalchemy import select

    from src.models.orm.organizations import Organization as OrganizationModel

    row = (
        await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.id == org_id)
        )
    ).scalar_one()
    row.is_provider = True
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)
    return row


@pytest.mark.asyncio
class TestOrganizationService:
    async def test_create_get_round_trip(self, db_session):
        from shared.sdk_organizations import create_organization, get_organization

        tag = uuid4().hex[:8]
        created = await create_organization(
            db_session,
            name=f"Acme {tag}",
            domain="ACME.COM",
            is_active=True,
            settings={"tier": "msp"},
            actor_email="admin@test.local",
        )
        assert created.name == f"Acme {tag}"
        assert created.domain == "acme.com"
        assert created.is_active is True
        assert created.settings == {"tier": "msp"}
        assert created.created_by == "admin@test.local"
        assert created.is_provider is False

        fetched = await get_organization(db_session, org_id=created.id)
        assert fetched.id == created.id
        assert fetched.name == f"Acme {tag}"

    async def test_create_domain_none_and_defaults(self, db_session):
        from shared.sdk_organizations import create_organization

        created = await create_organization(
            db_session,
            name=f"NoDomain {uuid4().hex[:8]}",
            domain=None,
            is_active=True,
            settings=None,
            actor_email="admin@test.local",
        )
        assert created.domain is None
        assert created.settings == {}

    async def test_get_missing_raises_404(self, db_session):
        from shared.sdk_organizations import get_organization

        with pytest.raises(OrganizationServiceError) as exc_info:
            await get_organization(db_session, org_id=uuid4())
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Organization not found"

    async def test_list_ordering_and_include_inactive(self, db_session):
        from shared.sdk_organizations import (
            create_organization,
            delete_organization,
            list_organizations,
        )

        tag = uuid4().hex[:8]
        beta = await create_organization(
            db_session, name=f"Beta {tag}", domain=None, is_active=True,
            settings=None, actor_email="a@t.local",
        )
        alpha = await create_organization(
            db_session, name=f"Alpha {tag}", domain=None, is_active=True,
            settings=None, actor_email="a@t.local",
        )
        assert alpha.id != beta.id
        provider = await create_organization(
            db_session, name=f"Zulu Provider {tag}", domain=None, is_active=True,
            settings=None, actor_email="a@t.local",
        )
        await _make_provider(db_session, provider.id)

        from src.models.orm.organizations import Organization as OrganizationModel
        from sqlalchemy import select

        row = (
            await db_session.execute(
                select(OrganizationModel).where(OrganizationModel.id == provider.id)
            )
        ).scalar_one()
        assert row.is_provider is True

        await delete_organization(db_session, org_id=beta.id)

        active_only = await list_organizations(db_session)
        names = [o.name for o in active_only]
        # Provider first even though "Zulu" sorts last alphabetically.
        provider_idx = names.index(f"Zulu Provider {tag}")
        alpha_idx = names.index(f"Alpha {tag}")
        assert provider_idx < alpha_idx
        assert f"Beta {tag}" not in names

        with_inactive = await list_organizations(db_session, include_inactive=True)
        names_all = [o.name for o in with_inactive]
        assert f"Beta {tag}" in names_all
        # Provider still first; inactive sorts after active.
        assert names_all.index(f"Zulu Provider {tag}") < names_all.index(f"Beta {tag}")

    async def test_update_partial_and_404_precedence(self, db_session):
        from shared.sdk_organizations import create_organization, update_organization

        created = await create_organization(
            db_session, name=f"Before {uuid4().hex[:8]}", domain="OLD.COM",
            is_active=True, settings={"a": 1}, actor_email="a@t.local",
        )
        updated = await update_organization(
            db_session, org_id=created.id, name="After",
        )
        assert updated.name == "After"
        assert updated.domain == "old.com"
        assert updated.settings == {"a": 1}

        updated2 = await update_organization(
            db_session, org_id=created.id, domain="NEW.COM",
            settings={"b": 2},
        )
        assert updated2.domain == "new.com"
        assert updated2.settings == {"b": 2}

        with pytest.raises(OrganizationServiceError) as exc_info:
            await update_organization(
                db_session, org_id=uuid4(), name="x"
            )
        assert exc_info.value.status_code == 404

    async def test_update_provider_cannot_be_disabled(self, db_session):
        from shared.sdk_organizations import create_organization, update_organization

        created = await create_organization(
            db_session, name=f"Prov {uuid4().hex[:8]}", domain=None,
            is_active=True, settings=None, actor_email="a@t.local",
        )
        await _make_provider(db_session, created.id)

        with pytest.raises(OrganizationServiceError) as exc_info:
            await update_organization(
                db_session, org_id=created.id, is_active=False,
            )
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Provider organization cannot be disabled"

        # Missing org still 404s even when the payload would disable.
        with pytest.raises(OrganizationServiceError) as exc_info:
            await update_organization(
                db_session, org_id=uuid4(), is_active=False,
            )
        assert exc_info.value.status_code == 404

    async def test_delete_soft_disables_and_404s(self, db_session):
        from shared.sdk_organizations import (
            create_organization,
            delete_organization,
            get_organization,
        )

        created = await create_organization(
            db_session, name=f"Gone {uuid4().hex[:8]}", domain=None,
            is_active=True, settings=None, actor_email="a@t.local",
        )
        assert await delete_organization(db_session, org_id=created.id) == created.name

        fetched = await get_organization(db_session, org_id=created.id)
        assert fetched.is_active is False

        with pytest.raises(OrganizationServiceError) as exc_info:
            await delete_organization(db_session, org_id=uuid4())
        assert exc_info.value.status_code == 404

    async def test_delete_provider_forbidden(self, db_session):
        from shared.sdk_organizations import create_organization, delete_organization

        created = await create_organization(
            db_session, name=f"ProvDel {uuid4().hex[:8]}", domain=None,
            is_active=True, settings=None, actor_email="a@t.local",
        )
        await _make_provider(db_session, created.id)

        with pytest.raises(OrganizationServiceError) as exc_info:
            await delete_organization(db_session, org_id=created.id)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Provider organization cannot be deleted"


@pytest.mark.asyncio
class TestOrganizationsRouterBoundary:
    """Handlers delegate to ``shared.sdk_organizations`` and map its errors."""

    async def test_list_delegates_flag(self):
        from src.routers.organizations import list_organizations

        with patch(
            "shared.sdk_organizations.list_organizations",
            new=AsyncMock(return_value=["ORG"]),
        ) as mock_list:
            result = await list_organizations(
                _stub_user(), AsyncMock(), include_inactive=True
            )
        assert result == ["ORG"]
        mock_list.assert_awaited_once()
        assert mock_list.call_args[1] == {"include_inactive": True}

    async def test_create_delegates_and_maps_error(self):
        from src.models import OrganizationCreate
        from src.routers.organizations import create_organization

        request = OrganizationCreate(
            name="N", domain="ACME.COM", is_active=True, settings={"a": 1}
        )
        with patch(
            "shared.sdk_organizations.create_organization",
            new=AsyncMock(return_value="ORG"),
        ) as mock_create:
            result = await create_organization(
                request, _stub_user("me@t.local"), AsyncMock()
            )
        assert result == "ORG"
        assert mock_create.call_args[1] == {
            "name": "N",
            "domain": "ACME.COM",
            "is_active": True,
            "settings": {"a": 1},
            "actor_email": "me@t.local",
        }

        with patch(
            "shared.sdk_organizations.create_organization",
            new=AsyncMock(
                side_effect=OrganizationServiceError(403, "nope")
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await create_organization(request, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 403

    async def test_get_maps_404(self):
        from src.routers.organizations import get_organization

        org_id = uuid4()
        with patch(
            "shared.sdk_organizations.get_organization",
            new=AsyncMock(return_value="ORG"),
        ) as mock_get:
            assert await get_organization(org_id, _stub_user(), AsyncMock()) == "ORG"
        mock_get.assert_awaited_once()
        assert mock_get.call_args[1] == {"org_id": org_id}

        with patch(
            "shared.sdk_organizations.get_organization",
            new=AsyncMock(
                side_effect=OrganizationServiceError(404, "Organization not found")
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_organization(org_id, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 404

    async def test_update_delegates_partial_fields(self):
        from src.models import OrganizationUpdate
        from src.routers.organizations import update_organization

        org_id = uuid4()
        request = OrganizationUpdate(name="New")
        with patch(
            "shared.sdk_organizations.update_organization",
            new=AsyncMock(return_value="ORG"),
        ) as mock_update:
            assert (
                await update_organization(org_id, request, _stub_user(), AsyncMock())
                == "ORG"
            )
        kwargs = mock_update.call_args[1]
        assert kwargs["org_id"] == org_id
        assert kwargs["name"] == "New"
        assert kwargs["domain"] is None
        assert kwargs["is_active"] is None
        assert kwargs["settings"] is None

    async def test_delete_maps_404(self):
        from src.routers.organizations import delete_organization

        org_id = uuid4()
        with patch(
            "shared.sdk_organizations.delete_organization",
            new=AsyncMock(return_value="N"),
        ):
            assert await delete_organization(org_id, _stub_user(), AsyncMock()) is None

        with patch(
            "shared.sdk_organizations.delete_organization",
            new=AsyncMock(
                side_effect=OrganizationServiceError(404, "Organization not found")
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_organization(org_id, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 404
