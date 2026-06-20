"""Every Manifest* field MUST be classified; metadata must be schema-safe."""
import pytest
from bifrost.field_classes import iter_manifest_models


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_every_field_is_classified(model):
    missing = [f for f in model.model_fields if "bifrost_field_class" not in (model.model_fields[f].json_schema_extra or {})]
    assert not missing, f"{model.__name__} untagged: {missing}"


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_metadata_is_schema_safe(model):
    # A callable left in json_schema_extra raises PydanticSerializationError here.
    model.model_json_schema()


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_predicate_keys_are_registered(model):
    # Codex round-2 P3: a tag referencing an unregistered predicate would KeyError at
    # runtime in field_class_of. Catch it here, statically, for every field.
    from bifrost.field_classes import PREDICATES
    for f in model.model_fields:
        extra = model.model_fields[f].json_schema_extra or {}
        key = extra.get("bifrost_class_predicate")
        if key is not None:
            assert key in PREDICATES, f"{model.__name__}.{f} references unregistered predicate {key!r}"
