import pytest

from src.models.contracts.policies import FilePolicies
from src.services.file_policy_service import FilePolicyService


@pytest.mark.asyncio
async def test_create_seeds_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    row = await svc.upsert_policy(
        organization_id=None,
        location="gallery",
        path="",
        policies=FilePolicies(policies=[]),
    )
    names = [r["name"] for r in row.policies["policies"]]
    assert "admin_bypass" in names


@pytest.mark.asyncio
async def test_create_does_not_duplicate_existing_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    doc = FilePolicies.model_validate(
        {
            "policies": [
                {
                    "name": "admin_bypass",
                    "actions": ["read"],
                    "when": {"user": "is_platform_admin"},
                }
            ]
        }
    )
    row = await svc.upsert_policy(
        organization_id=None, location="gallery", path="", policies=doc
    )
    names = [r["name"] for r in row.policies["policies"]]
    assert names.count("admin_bypass") == 1


@pytest.mark.asyncio
async def test_update_does_not_re_add_revoked_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    await svc.upsert_policy(
        organization_id=None,
        location="gallery",
        path="",
        policies=FilePolicies(policies=[]),
    )
    # Admin revokes admin_bypass on update:
    revoked = FilePolicies.model_validate(
        {
            "policies": [
                {
                    "name": "team",
                    "actions": ["read"],
                    "when": {"user": "is_platform_admin"},
                }
            ]
        }
    )
    row = await svc.upsert_policy(
        organization_id=None, location="gallery", path="", policies=revoked
    )
    names = [r["name"] for r in row.policies["policies"]]
    assert "admin_bypass" not in names
