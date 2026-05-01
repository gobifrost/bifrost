"""Round-trip TablePublic ↔ ORM dict for the policies field."""

from src.models.contracts.tables import TableCreate, TablePublic


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
        "application_id": None,
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
