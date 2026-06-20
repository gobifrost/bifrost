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
