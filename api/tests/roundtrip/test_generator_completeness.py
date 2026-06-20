"""Risk-1: generator sets a non-default sentinel for EVERY field, and the result validates."""
import pytest
from bifrost.field_classes import iter_manifest_models
from tests.roundtrip.generators import all_fields_populated


@pytest.mark.parametrize("model", iter_manifest_models(), ids=lambda m: m.__name__)
def test_all_fields_populated(model):
    fx = all_fields_populated(model)
    assert set(fx) == set(model.model_fields), (
        f"{model.__name__} missing {set(model.model_fields) - set(fx)}"
    )
    model.model_validate(fx)  # alias-aware: uses populate_by_name where set
