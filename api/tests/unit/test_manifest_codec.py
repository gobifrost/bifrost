import pytest
from bifrost.field_classes import classify, import_owner_of, FieldClass
from bifrost.manifest_codec import Destination, EntityCodec, ImportFields
from pydantic import BaseModel, Field


def test_classify_records_import_owner():
    class M(BaseModel):
        a: str = Field(**classify(FieldClass.CONTENT, import_owner="indexer"))
        b: str = Field(**classify(FieldClass.CONTENT))  # default
    assert import_owner_of(M, "a") == "indexer"
    assert import_owner_of(M, "b") == "direct"


def test_view_git_sync_dumps_whole_model_including_nones():
    class M(EntityCodec, BaseModel):
        id: str = Field(**classify(FieldClass.IDENTITY))
        path: str | None = Field(default=None, **classify(FieldClass.CONTENT))
    m = M(id="x")
    # GIT_SYNC == model_dump() verbatim: every field present, None included.
    assert m.view(Destination.GIT_SYNC) == {"id": "x", "path": None}


def test_import_fields_shape():
    f = ImportFields(indexer_content={}, direct={"a": 1}, restamp={})
    assert f.direct == {"a": 1} and f.indexer_content == {} and f.restamp == {}


def assert_parity(produced: dict, legacy: dict, *, label: str = "") -> None:
    """Byte-parity assertion for entity conversions: key-set first, then values."""
    only_new = set(produced) - set(legacy)
    only_old = set(legacy) - set(produced)
    assert not only_new and not only_old, (
        f"{label} field-set mismatch: only_new={only_new} only_old={only_old}"
    )
    assert produced == legacy, f"{label} values diverge:\n produced={produced}\n legacy={legacy}"


def test_assert_parity_passes_on_equal_and_fails_on_diff():
    assert_parity({"a": 1}, {"a": 1}, label="ok")
    with pytest.raises(AssertionError):
        assert_parity({"a": 1}, {"a": 2}, label="bad")
    with pytest.raises(AssertionError):
        assert_parity({"a": 1, "b": 2}, {"a": 1}, label="extra")


@pytest.mark.e2e
async def test_organization_git_sync_parity(db_session):
    import uuid
    from sqlalchemy import delete
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestOrganization
    from bifrost.manifest_codec import Destination

    org = Organization(id=uuid.uuid4(), name="RT Org Parity", is_active=True, created_by="test")
    db_session.add(org)
    await db_session.commit()

    try:
        expected = {"id": str(org.id), "name": "RT Org Parity", "is_active": True}
        produced = ManifestOrganization.from_row(org).view(Destination.GIT_SYNC)
        assert_parity(produced, expected, label="organization git_sync")
    finally:
        await db_session.execute(delete(Organization).where(Organization.id == org.id))
        await db_session.commit()


@pytest.mark.e2e
async def test_role_git_sync_parity(db_session):
    import uuid
    from sqlalchemy import delete
    from src.models.orm.users import Role
    from bifrost.manifest import ManifestRole
    from bifrost.manifest_codec import Destination

    role = Role(id=uuid.uuid4(), name="rt_role_parity", created_by="test")
    db_session.add(role)
    await db_session.commit()

    try:
        expected = {"id": str(role.id), "name": "rt_role_parity"}
        produced = ManifestRole.from_row(role).view(Destination.GIT_SYNC)
        assert_parity(produced, expected, label="role git_sync")
    finally:
        await db_session.execute(delete(Role).where(Role.id == role.id))
        await db_session.commit()
