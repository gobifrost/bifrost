"""E2E: a workflow names the identity it runs as when no person starts it.

`run_identity_id` accepts an identity of the workflow's own organization; a
global or provider-organization workflow may name the global identity or a
provider-organization identity. Null means the organization's default
identity. Nothing runs as it yet.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from shared.identities import IdentityKind
from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.users import User
from tests.e2e.conftest import write_and_register

pytestmark = pytest.mark.e2e

_SOURCE = '''
from bifrost import workflow


@workflow
def {name}():
    return "ok"
'''


def _register(e2e_client, admin, organization_id) -> str:
    name = f"run_identity_{uuid.uuid4().hex[:8]}"
    workflow = write_and_register(
        e2e_client,
        admin.headers,
        f"workflows/{name}.py",
        _SOURCE.format(name=name),
        name,
        organization_id=organization_id,
    )
    return workflow["id"]


async def _identities(db_session, org1, org2) -> dict[str, str]:
    rows = (await db_session.scalars(select(User).where(User.identity_kind.is_not(None)))).all()
    by_org = {(row.organization_id, row.identity_kind): str(row.id) for row in rows}
    return {
        "global": by_org[(None, IdentityKind.GLOBAL_DEFAULT)],
        "provider": by_org[(PROVIDER_ORG_ID, IdentityKind.ORG_DEFAULT)],
        "org1": by_org[(uuid.UUID(org1["id"]), IdentityKind.ORG_DEFAULT)],
        "org2": by_org[(uuid.UUID(org2["id"]), IdentityKind.ORG_DEFAULT)],
    }


def _patch(e2e_client, admin, workflow_id: str, body: dict):
    return e2e_client.patch(f"/api/workflows/{workflow_id}", headers=admin.headers, json=body)


@pytest.mark.asyncio
async def test_global_workflow_runs_as_global_or_provider_identity(
    e2e_client, platform_admin, org1, org2, db_session
) -> None:
    identities = await _identities(db_session, org1, org2)
    workflow_id = _register(e2e_client, platform_admin, None)

    for allowed in ("provider", "global"):
        response = _patch(e2e_client, platform_admin, workflow_id, {"run_identity_id": identities[allowed]})
        assert response.status_code == 200, response.text
        assert response.json()["run_identity_id"] == identities[allowed]

    refused = _patch(e2e_client, platform_admin, workflow_id, {"run_identity_id": identities["org1"]})
    assert refused.status_code == 422, refused.text
    assert "run_identity_id" in refused.text

    cleared = _patch(e2e_client, platform_admin, workflow_id, {"run_identity_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["run_identity_id"] is None


@pytest.mark.asyncio
async def test_organization_workflow_runs_only_as_its_own_identities(
    e2e_client, platform_admin, org1, org2, org1_user, db_session
) -> None:
    identities = await _identities(db_session, org1, org2)
    workflow_id = _register(e2e_client, platform_admin, org1["id"])

    allowed = _patch(e2e_client, platform_admin, workflow_id, {"run_identity_id": identities["org1"]})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["run_identity_id"] == identities["org1"]

    for refused_id in (identities["org2"], identities["provider"], str(org1_user.user_id), str(uuid.uuid4())):
        refused = _patch(e2e_client, platform_admin, workflow_id, {"run_identity_id": refused_id})
        assert refused.status_code == 422, (refused_id, refused.text)
        assert "run_identity_id" in refused.text

    shown = e2e_client.get(f"/api/workflows/{workflow_id}", headers=platform_admin.headers)
    assert shown.status_code == 200, shown.text
    assert shown.json()["run_identity_id"] == identities["org1"]


@pytest.mark.asyncio
async def test_moving_a_workflow_keeps_its_identity_allowed(
    e2e_client, platform_admin, org1, org2, db_session
) -> None:
    identities = await _identities(db_session, org1, org2)
    workflow_id = _register(e2e_client, platform_admin, org1["id"])
    assert _patch(e2e_client, platform_admin, workflow_id, {"run_identity_id": identities["org1"]}).status_code == 200

    moved_alone = _patch(e2e_client, platform_admin, workflow_id, {"organization_id": org2["id"]})
    assert moved_alone.status_code == 422, moved_alone.text
    assert "run_identity_id" in moved_alone.text

    moved_together = _patch(
        e2e_client,
        platform_admin,
        workflow_id,
        {"organization_id": org2["id"], "run_identity_id": identities["org2"]},
    )
    assert moved_together.status_code == 200, moved_together.text
    body = moved_together.json()
    assert (body["organization_id"], body["run_identity_id"]) == (org2["id"], identities["org2"])
