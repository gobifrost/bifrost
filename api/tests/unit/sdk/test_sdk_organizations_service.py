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
from src.services.authorization.reach import EVERYTHING
from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.organizations import Organization as OrganizationModel


def _stub_user(email: str = "admin@test.local"):
    return SimpleNamespace(email=email)


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
        provider = await db_session.get(OrganizationModel, PROVIDER_ORG_ID)
        assert provider is not None
        provider.name = f"Zulu Provider {tag}"
        await db_session.flush()

        await delete_organization(db_session, org_id=beta.id)

        active_only = await list_organizations(db_session, reach=EVERYTHING)
        names = [o.name for o in active_only]
        # Provider first even though "Zulu" sorts last alphabetically.
        provider_idx = names.index(f"Zulu Provider {tag}")
        alpha_idx = names.index(f"Alpha {tag}")
        assert provider_idx < alpha_idx
        assert f"Beta {tag}" not in names

        with_inactive = await list_organizations(
            db_session, reach=EVERYTHING, include_inactive=True
        )
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
        from shared.sdk_organizations import update_organization

        created = await db_session.get(OrganizationModel, PROVIDER_ORG_ID)
        assert created is not None

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
        from shared.sdk_organizations import delete_organization

        created = await db_session.get(OrganizationModel, PROVIDER_ORG_ID)
        assert created is not None

        with pytest.raises(OrganizationServiceError) as exc_info:
            await delete_organization(db_session, org_id=created.id)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Provider organization cannot be deleted"


@pytest.fixture
def admin_caller():
    from tests.helpers.authorization import admin_caller, handlers_as

    with handlers_as(admin_caller(), "src.routers.organizations") as caller:
        yield caller


@pytest.mark.asyncio
@pytest.mark.usefixtures("admin_caller")
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
        assert mock_list.call_args[1] == {"reach": EVERYTHING, "include_inactive": True}

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
            "tags": [],
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
        assert kwargs["tags"] is None

    async def test_bulk_tags_delegates_after_authorizing_every_org(self):
        from src.models import OrganizationTagsBulkUpdate
        from src.routers.organizations import bulk_update_organization_tags

        org_ids = [uuid4(), uuid4()]
        request = OrganizationTagsBulkUpdate(
            organization_ids=org_ids, add=["MSP"], remove=["Old"]
        )
        with patch(
            "shared.sdk_organizations.bulk_update_organization_tags",
            new=AsyncMock(return_value=["ORG"]),
        ) as mock_bulk:
            assert await bulk_update_organization_tags(
                request, _stub_user(), AsyncMock()
            ) == ["ORG"]
        assert mock_bulk.call_args[1] == {
            "organization_ids": org_ids,
            "add": ["MSP"],
            "remove": ["Old"],
        }

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


class TestOrganizationTagContracts:
    def test_tags_are_trimmed_and_deduplicated_case_insensitively(self):
        from src.models import OrganizationUpdate

        update = OrganizationUpdate(tags=["  MSP ", "msp", "", "Licence  Tier", "Connectivity"])
        assert update.tags == ["MSP", "Licence Tier", "Connectivity"]
        assert OrganizationUpdate().tags is None

    def test_overlong_tag_rejected(self):
        from pydantic import ValidationError

        from src.models import OrganizationUpdate

        with pytest.raises(ValidationError):
            OrganizationUpdate(tags=["x" * 51])

    def test_bulk_requires_a_change(self):
        from pydantic import ValidationError

        from src.models import OrganizationTagsBulkUpdate

        with pytest.raises(ValidationError):
            OrganizationTagsBulkUpdate(organization_ids=[uuid4()], add=[" "], remove=[])


@pytest.mark.asyncio
class TestOrganizationTagService:
    async def test_create_and_update_replace_tags(self, db_session):
        from shared.sdk_organizations import create_organization, update_organization

        created = await create_organization(
            db_session, name=f"Tagged {uuid4().hex[:8]}", domain=None,
            tags=["MSP"], actor_email="a@t.local",
        )
        assert created.tags == ["MSP"]

        renamed = await update_organization(db_session, org_id=created.id, name="Renamed")
        assert renamed.tags == ["MSP"]

        retagged = await update_organization(
            db_session, org_id=created.id, tags=["Licence", "Connectivity"]
        )
        assert retagged.tags == ["Licence", "Connectivity"]

    async def test_bulk_add_and_remove(self, db_session):
        from shared.sdk_organizations import (
            bulk_update_organization_tags,
            create_organization,
        )

        first = await create_organization(
            db_session, name=f"First {uuid4().hex[:8]}", domain=None,
            tags=["msp", "Legacy"], actor_email="a@t.local",
        )
        second = await create_organization(
            db_session, name=f"Second {uuid4().hex[:8]}", domain=None,
            actor_email="a@t.local",
        )

        result = await bulk_update_organization_tags(
            db_session,
            organization_ids=[second.id, first.id],
            add=["MSP", "Licence"],
            remove=["legacy"],
        )
        assert [org.id for org in result] == [second.id, first.id]
        assert result[0].tags == ["MSP", "Licence"]
        # The existing spelling wins over a case-insensitive duplicate.
        assert result[1].tags == ["msp", "Licence"]

    async def test_bulk_missing_org_changes_nothing(self, db_session):
        from shared.sdk_organizations import (
            bulk_update_organization_tags,
            create_organization,
            get_organization,
        )

        org = await create_organization(
            db_session, name=f"Keep {uuid4().hex[:8]}", domain=None,
            actor_email="a@t.local",
        )
        with pytest.raises(OrganizationServiceError) as exc_info:
            await bulk_update_organization_tags(
                db_session, organization_ids=[org.id, uuid4()], add=["MSP"], remove=[],
            )
        assert exc_info.value.status_code == 404
        assert (await get_organization(db_session, org_id=org.id)).tags == []


@pytest.mark.asyncio
class TestOrganizationTagAuthorization:
    async def test_bulk_tags_refuses_an_org_outside_the_callers_reach(self):
        from src.models import OrganizationTagsBulkUpdate
        from src.routers.organizations import bulk_update_organization_tags
        from src.services.authorization.context import (
            AuthorizationContext,
            Boundary,
            BoundaryKind,
            RoleGrant,
        )
        from src.services.authorization.enforce import Caller
        from src.core.principal import UserPrincipal
        from shared.builtin_roles import USER_ROLE_ID
        from tests.helpers.authorization import handlers_as

        allowed, other = uuid4(), uuid4()
        principal = UserPrincipal(
            user_id=uuid4(), email="tech@example.com", organization_id=allowed,
            name="Tech", is_superuser=False, is_verified=True,
        )
        caller = Caller(
            principal,
            AuthorizationContext(
                user_id=principal.user_id,
                home_organization_id=allowed,
                base_role_id=USER_ROLE_ID,
                is_external=False,
                base_permissions=frozenset(),
                role_grants=(
                    RoleGrant(
                        uuid4(),
                        frozenset({"organizations.readwrite"}),
                        (Boundary(BoundaryKind.ORGANIZATION, allowed),),
                    ),
                ),
            ),
        )
        request = OrganizationTagsBulkUpdate(organization_ids=[allowed, other], add=["MSP"])
        with handlers_as(caller, "src.routers.organizations"), patch(
            "shared.sdk_organizations.bulk_update_organization_tags",
            new=AsyncMock(return_value=[]),
        ) as mock_bulk:
            with pytest.raises(HTTPException) as exc_info:
                await bulk_update_organization_tags(request, _stub_user(), AsyncMock())
        assert exc_info.value.status_code == 403
        mock_bulk.assert_not_awaited()
