"""Round-trip TablePublic ↔ ORM dict for the policies field."""

import pytest

from src.models.contracts.tables import TableCreate, TablePublic, TableUpdate


def test_create_accepts_policies():
    raw = {
        "name": "t1",
        "policies": {
            "policies": [
                {"name": "p1", "actions": ["read"], "when": None},
            ]
        },
    }
    tc = TableCreate.model_validate(raw)
    assert tc.policies is not None
    assert tc.policies.policies[0].name == "p1"


def test_public_maps_access_to_policies():
    """TablePublic reads the ORM column 'access' as 'policies'."""
    orm_dict = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "t1",
        "organization_id": None,
        "schema": None,
        "description": None,
        "access": {  # ORM column name
            "policies": [
                {"name": "p1", "actions": ["read"], "when": None}
            ]
        },
        "created_at": "2026-04-30T00:00:00Z",
        "updated_at": "2026-04-30T00:00:00Z",
        "created_by": None,
    }
    tp = TablePublic.model_validate(orm_dict)
    assert tp.policies is not None
    assert tp.policies.policies[0].name == "p1"


def test_public_maps_access_to_policies_from_orm_object():
    """TablePublic.model_validate works against an ORM-shaped object (not a dict).

    This is the path Pydantic v2 takes when from_attributes=True is set
    and the caller passes the ORM row directly (e.g., REST response path).
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    class FakeOrmTable:
        def __init__(self):
            self.id = uuid4()
            self.name = "t1"
            self.organization_id = None
            self.schema = None
            self.description = None
            self.access = {  # ORM column name
                "policies": [
                    {"name": "p1", "actions": ["read"], "when": None}
                ]
            }
            self.created_at = datetime(2026, 4, 30, tzinfo=timezone.utc)
            self.updated_at = datetime(2026, 4, 30, tzinfo=timezone.utc)
            self.created_by = None

    tp = TablePublic.model_validate(FakeOrmTable())
    assert tp.policies is not None
    assert tp.policies.policies[0].name == "p1"


def test_update_distinguishes_clear_from_unset():
    """TableUpdate must distinguish 'clear policies' from 'don't touch policies'.

    Repository code at api/src/routers/tables.py uses
    `if "policies" in data.model_fields_set` to make this distinction.
    """
    explicit_clear = TableUpdate(policies=None)
    assert "policies" in explicit_clear.model_fields_set

    untouched = TableUpdate()
    assert "policies" not in untouched.model_fields_set


def test_public_outputs_policies_field_name():
    """TablePublic.model_dump() must emit 'policies', not 'access'.

    The OpenAPI spec and TS types depend on this output name.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    tp = TablePublic.model_validate({
        "id": uuid4(),
        "name": "t1",
        "organization_id": None,
        "schema": None,
        "description": None,
        "access": {"policies": [{"name": "p1", "actions": ["read"], "when": None}]},
        "created_at": datetime(2026, 4, 30, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 4, 30, tzinfo=timezone.utc),
        "created_by": None,
    })
    dumped = tp.model_dump(mode="json")
    assert "policies" in dumped
    assert "access" not in dumped
    assert dumped["policies"]["policies"][0]["name"] == "p1"


@pytest.mark.asyncio
async def test_load_policies_corruption_returns_empty(caplog):
    """load_resolved_table_policies fails closed (empty TablePolicies → default deny)
    when JSONB is corrupt, with a warning log so corruption is visible."""
    from unittest.mock import AsyncMock
    from src.services.table_policy_loader import load_resolved_table_policies

    class FakeTable:
        access = {"policies": [{"name": "p", "actions": ["read"], "when": {"INVALID_OP": []}}]}
        id = "fake-id"
        organization_id = None
        solution_id = None

    fake_db = AsyncMock()

    with caplog.at_level("WARNING", logger="src.services.table_policy_loader"):
        result = await load_resolved_table_policies(FakeTable(), fake_db)  # type: ignore[arg-type]

    assert result.policies == []  # default deny
    assert any(
        rec.name == "src.services.table_policy_loader" and "malformed" in rec.message
        for rec in caplog.records
    )


def test_policy_ref_serializes_with_dollar_ref_alias():
    """A $ref table policy must persist as {"$ref": ...}, not {"ref": ...}.

    Regression: api/src/repositories/tables.py stored TablePolicies via
    model_dump(mode="json") WITHOUT by_alias=True, dropping the $ref alias.
    The corrupted {"ref": ...} shape then exploded ManifestTable.from_row
    (ManifestPolicy requires name+actions), surfacing as an order-dependent
    flake when an orphaned table row leaked into a later generate_manifest().
    """
    tc = TableCreate.model_validate({"name": "t", "policies": {"policies": [{"$ref": "admin_bypass"}]}})
    assert tc.policies is not None
    dumped = tc.policies.model_dump(mode="json", by_alias=True)
    assert dumped == {"policies": [{"$ref": "admin_bypass"}]}, dumped


def test_manifest_table_round_trips_ref_policy():
    """ManifestTable.from_row accepts a stored $ref table policy and preserves it."""
    from bifrost.manifest import ManifestPolicyRef, ManifestTable

    class FakeOrmTable:
        id = "00000000-0000-0000-0000-000000000009"
        name = "reffed"
        description = None
        schema = None
        organization_id = None
        solution_id = None
        access = {"policies": [{"$ref": "admin_bypass"}]}

    mt = ManifestTable.from_row(FakeOrmTable())
    assert mt.policies is not None
    assert len(mt.policies) == 1
    assert isinstance(mt.policies[0], ManifestPolicyRef)
    assert mt.policies[0].ref == "admin_bypass"


def test_manifest_table_tolerates_legacy_unaliased_ref_policy():
    """Defense-in-depth: rows already corrupted as {"ref": ...} by the old
    serializer must not explode generate_manifest(). Production DBs already
    contain such rows; the serializer recovers them as a ref, not a crash."""
    from bifrost.manifest import ManifestPolicyRef, ManifestTable

    class FakeOrmTable:
        id = "00000000-0000-0000-0000-00000000000a"
        name = "legacy-reffed"
        description = None
        schema = None
        organization_id = None
        solution_id = None
        access = {"policies": [{"ref": "admin_bypass"}]}  # legacy, un-aliased

    mt = ManifestTable.from_row(FakeOrmTable())
    assert mt.policies is not None
    assert isinstance(mt.policies[0], ManifestPolicyRef)
    assert mt.policies[0].ref == "admin_bypass"
