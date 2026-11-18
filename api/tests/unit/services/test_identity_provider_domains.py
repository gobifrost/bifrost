"""Domain claim verification against the database."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.identity_providers import OrganizationDomain
from src.models.orm.organizations import Organization
from src.services.identity_provisioning import domains


@pytest.mark.asyncio
async def test_a_domain_another_org_verified_is_a_conflict_and_the_transaction_survives(
    db_session,
):
    suffix = uuid4().hex[:10]
    domain = f"contested-{suffix}.example"
    first = Organization(name=f"First {suffix}", created_by="test")
    second = Organization(name=f"Second {suffix}", created_by="test")
    db_session.add_all([first, second])
    await db_session.flush()
    db_session.add(
        OrganizationDomain(
            organization_id=first.id,
            domain=domain,
            verification_token="first",
            verified_at=datetime.now(timezone.utc),
            created_by="test",
        )
    )
    claim = OrganizationDomain(
        organization_id=second.id, domain=domain, verification_token="second", created_by="test"
    )
    db_session.add(claim)
    await db_session.flush()

    with patch.object(
        domains, "lookup_txt_records", AsyncMock(return_value=[domains.txt_record_value(claim)])
    ):
        with pytest.raises(domains.DomainClaimError) as raised:
            await domains.verify_claim(db_session, claim)

    assert raised.value.status_code == 409
    # Only the savepoint rolled back: the request can still read and write.
    await db_session.refresh(claim)
    assert claim.verified_at is None
    assert claim.last_checked_at is not None
    assert await db_session.scalar(
        select(OrganizationDomain.id).where(OrganizationDomain.id == claim.id)
    ) == claim.id
