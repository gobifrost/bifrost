import pytest

from src.models.contracts.policies import FilePolicies
from src.services.file_policy_service import FilePolicyService


def _refs(policies_doc: dict) -> list[str]:
    """Extract $ref values from the policies list."""
    return [r["$ref"] for r in policies_doc.get("policies", []) if "$ref" in r]


@pytest.mark.asyncio
async def test_create_seeds_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    row = await svc.upsert_policy(
        organization_id=None,
        location="gallery",
        path="",
        policies=FilePolicies(policies=[]),
    )
    assert "admin_bypass" in _refs(row.policies)


@pytest.mark.asyncio
async def test_create_does_not_duplicate_existing_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    # An existing $ref to admin_bypass should not be duplicated.
    doc = FilePolicies.model_validate({"policies": [{"$ref": "admin_bypass"}]})
    row = await svc.upsert_policy(
        organization_id=None, location="gallery", path="", policies=doc
    )
    assert _refs(row.policies).count("admin_bypass") == 1


@pytest.mark.asyncio
async def test_create_does_not_duplicate_inline_name_admin_bypass(db_session):
    svc = FilePolicyService(db_session)
    # An inline rule with name=="admin_bypass" (legacy body form) must also prevent
    # the seed from being prepended, so admin_bypass appears exactly once.
    doc = FilePolicies.model_validate(
        {
            "policies": [
                {
                    "name": "admin_bypass",
                    "actions": ["read", "write", "delete", "list"],
                    "when": {"user": "is_platform_admin"},
                }
            ]
        }
    )
    row = await svc.upsert_policy(
        organization_id=None, location="gallery", path="", policies=doc
    )
    # The name== arm of the idempotency check should fire; seed must NOT be prepended.
    admin_bypass_entries = [
        r for r in row.policies.get("policies", [])
        if r.get("name") == "admin_bypass" or r.get("$ref") == "admin_bypass"
    ]
    assert len(admin_bypass_entries) == 1


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
    assert "admin_bypass" not in _refs(row.policies)
