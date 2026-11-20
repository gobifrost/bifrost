"""A disabled organization stops provisioning and gives up its domains."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from shared.sdk_organizations import delete_organization
from src.models.orm.identity_providers import IdentityProvider, OrganizationDomain
from src.models.orm.organizations import Organization
from src.services.identity_provisioning import tokens


async def _organization(session) -> Organization:
    organization = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="test")
    session.add(organization)
    await session.flush()
    return organization


@pytest.mark.asyncio
async def test_a_disabled_organizations_connection_is_refused(db_session):
    organization = await _organization(db_session)
    connection = IdentityProvider(
        organization_id=organization.id,
        name="Contoso Entra",
        source_type="scim",
        vendor="entra",
        created_by="test",
    )
    db_session.add(connection)
    await db_session.flush()
    raw, _ = await tokens.issue_token(db_session, connection, lifetime_days=90, created_by="test")
    assert await tokens.authenticate(db_session, connection.id, raw) is not None

    organization.is_active = False
    await db_session.flush()

    assert await tokens.authenticate(db_session, connection.id, raw) is None


@pytest.mark.asyncio
async def test_disabling_an_organization_releases_its_domains(db_session):
    domain = f"contoso-{uuid4().hex[:8]}.example"
    first = await _organization(db_session)
    second = await _organization(db_session)
    db_session.add(
        OrganizationDomain(
            organization_id=first.id,
            domain=domain,
            verification_token="t",
            verified_at=datetime.now(timezone.utc),
            created_by="test",
        )
    )
    await db_session.flush()

    await delete_organization(db_session, org_id=first.id)

    assert (
        await db_session.scalar(
            select(OrganizationDomain).where(OrganizationDomain.organization_id == first.id)
        )
        is None
    )
    # The domain is free for another organization to verify.
    db_session.add(
        OrganizationDomain(
            organization_id=second.id,
            domain=domain,
            verification_token="t",
            verified_at=datetime.now(timezone.utc),
            created_by="test",
        )
    )
    await db_session.flush()
