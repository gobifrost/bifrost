"""An install's config VALUE resolves at runtime in the install's scope.

VERIFIED, NO RESOLUTION CODE NEEDED. A Solution only *declares* config keys
(``SolutionConfigSchema`` — owned/portable, no value). The VALUE for an install
is a plain instance-owned ``Config`` row in the install's org, set via the
normal config path; it carries no ``solution_id`` and is never in the bundle.

Because the install's value is just an ordinary org-scoped ``Config`` row, the
EXISTING org→global cascade in ``ConfigRepository`` already resolves it when the
SDK reader is in the install's org. ``merged_for_sdk()`` is the exact method the
``/api/sdk/config`` endpoint uses (see ``src/routers/cli.py``). This test proves
the value round-trips through that path; the ``SolutionConfigSchema`` declaration
is incidental to resolution and only documents that the key belongs to the
install.
"""
import pytest
from uuid import uuid4

from src.models.contracts.config import SetConfigRequest
from src.models.orm.config import ConfigType
from src.models.orm.organizations import Organization
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solutions import Solution
from src.repositories.config import ConfigRepository


@pytest.mark.e2e
async def test_install_value_resolves_in_install_scope(db_session) -> None:
    db = db_session
    org = Organization(id=uuid4(), name=f"Org-{uuid4().hex[:6]}", created_by="op@test")
    db.add(org)
    org_id = org.id

    sol = Solution(id=uuid4(), slug=f"res-{uuid4().hex[:8]}", name="R", organization_id=org_id)
    db.add(sol)
    await db.flush()
    db.add(SolutionConfigSchema(id=uuid4(), solution_id=sol.id, key="REGION", type="string"))
    await db.flush()

    # Install value: a normal org-scoped Config row, no solution_id.
    repo = ConfigRepository(db, org_id=org_id, bypass_resource_admission=True)
    await repo.set_config(
        SetConfigRequest(key="REGION", value="us-west", type=ConfigType.STRING, organization_id=org_id),
        updated_by="op@test",
    )
    await db.flush()

    # SDK reader in the install's org sees the value via the existing cascade.
    reader = ConfigRepository(db, org_id=org_id, bypass_resource_admission=True)
    merged = await reader.merged_for_sdk()
    assert merged["REGION"]["value"] == "us-west"
