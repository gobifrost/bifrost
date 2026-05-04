"""Round-trip TablePublic ↔ ORM dict for the policies field."""

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


def test_load_policies_corruption_returns_empty(caplog):
    """_load_policies fails closed (empty TablePolicies → default deny)
    when JSONB is corrupt, with a warning log so corruption is visible."""
    from src.routers.tables import _load_policies

    class FakeTable:
        access = {"policies": [{"name": "p", "actions": ["read"], "when": {"INVALID_OP": []}}]}
        id = "fake-id"

    with caplog.at_level("WARNING", logger="src.routers.tables"):
        result = _load_policies(FakeTable())  # type: ignore[arg-type]

    assert result.policies == []  # default deny
    assert any(
        rec.name == "src.routers.tables" and "malformed policies" in rec.message
        for rec in caplog.records
    )
