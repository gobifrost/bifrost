"""Exact lookup over the real HTTP boundary, using existing caller reach."""

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete

from src.models.orm.integrations import Integration, IntegrationMapping

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def lookup_integration(db_session, org1, org2):
    integration = Integration(name=f"exact-lookup-{uuid4().hex}")
    db_session.add(integration)
    await db_session.flush()
    for org, entity in ((org1, "visible"), (org2, "hidden")):
        db_session.add(IntegrationMapping(
            integration_id=integration.id,
            organization_id=UUID(org["id"]),
            entity_id=entity,
        ))
    await db_session.commit()
    try:
        yield integration
    finally:
        await db_session.execute(delete(Integration).where(Integration.id == integration.id))
        await db_session.commit()


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [("visible", "unique"), ("missing", "not_found_in_authorized_scope"),
     ("hidden", "not_found_in_authorized_scope")],
)
def test_exact_lookup_respects_organization_access(
    e2e_client, org1_service_headers, org1, lookup_integration, entity_id, expected,
):
    response = e2e_client.post(
        "/api/sdk/integrations/find_mapping",
        headers=org1_service_headers,
        json={"name": lookup_integration.name, "entity_id": entity_id},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == expected
    assert result["receipt"] == {
        "operation": "integrations.mappings.list", "searched": "authorized_reach",
        "candidates_considered": 1, "entity_id": entity_id,
    }
    if expected == "unique":
        assert result["mapping"]["organization_id"] == org1["id"]
        assert result["mapping"]["entity_id"] == entity_id
    else:
        assert result["mapping"] is None


@pytest.mark.asyncio
async def test_duplicate_visible_mappings_return_no_mapping(
    e2e_client, platform_admin, db_session, lookup_integration,
):
    from sqlalchemy import update

    await db_session.execute(
        update(IntegrationMapping)
        .where(IntegrationMapping.integration_id == lookup_integration.id)
        .values(entity_id="duplicate")
    )
    await db_session.commit()
    response = e2e_client.post(
        "/api/sdk/integrations/find_mapping", headers=platform_admin.headers,
        json={"name": lookup_integration.name, "entity_id": "duplicate"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ambiguous"
    assert response.json()["mapping"] is None
    assert response.json()["receipt"]["candidates_considered"] == 2


def test_caller_without_integration_reach_is_refused(
    e2e_client, org1_user, lookup_integration,
):
    response = e2e_client.post(
        "/api/sdk/integrations/find_mapping", headers=org1_user.headers,
        json={"name": lookup_integration.name, "entity_id": "visible"},
    )
    assert response.status_code == 403, response.text
