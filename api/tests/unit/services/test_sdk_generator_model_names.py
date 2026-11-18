"""Schema identities must survive Python model-name collisions."""
import ast
from copy import deepcopy

from typing import get_type_hints
import sys
import types

import pytest

from src.services.sdk_generator import extract_models_and_methods, generate_sdk


def obj(**properties):
    return {"type": "object", "properties": properties}


@pytest.mark.parametrize("identical", [False, True])
def test_component_and_inline_collision_preserves_both_shapes(identical):
    first = obj(left={"type": "string"})
    second = deepcopy(first) if identical else obj(right={"type": "integer"})
    spec = {
        "info": {"title": "Example"},
        "components": {"schemas": {
            "Parent": obj(child=first),
            "ParentChild": second,
            "Consumer": obj(value={"$ref": "#/components/schemas/ParentChild"}),
        }},
    }
    models, _ = extract_models_and_methods(spec, "Example")
    by_name = {model.name: model for model in models}
    assert len(by_name) == len(models)
    inline_name = by_name["Parent"].fields[0].type.removeprefix("Optional[").removesuffix("]")
    component_name = by_name["Consumer"].fields[0].type.removeprefix("Optional[").removesuffix("]")
    assert inline_name != component_name
    assert by_name[inline_name].fields[0].name == "left"
    assert by_name[component_name].fields[0].name == ("left" if identical else "right")
    code, _ = generate_sdk(spec, "example", "bearer")
    names = [node.name for node in ast.parse(code).body if isinstance(node, ast.ClassDef)]
    assert len(names) == len(set(names))
    module = types.ModuleType("_collision_sdk")
    sys.modules[module.__name__] = module
    try:
        exec(code, module.__dict__)
        inline_type = get_type_hints(module.Parent)["child"].__args__[0]
        component_type = get_type_hints(module.Consumer)["value"].__args__[0]
        assert inline_type.from_dict({"left": "yes"}).left == "yes"
        field = "left" if identical else "right"
        assert getattr(component_type.from_dict({field: 7}), field) == 7
    finally:
        del sys.modules[module.__name__]
    reversed_spec = deepcopy(spec)
    reversed_spec["components"]["schemas"] = dict(reversed(list(spec["components"]["schemas"].items())))
    assert generate_sdk(reversed_spec, "example", "bearer")[0] == code


def test_nested_and_response_collisions_use_the_registered_types():
    spec = {"info": {"title": "Example"}, "components": {"schemas": {
        "A": obj(bC=obj(x={"type": "string"})),
        "AB": obj(c=obj(y={"type": "integer"})),
        "ReadResponse": obj(z={"type": "boolean"}),
        "Plain": obj(value={"type": "string"}),
    }}, "paths": {"/items": {"get": {"operationId": "read", "responses": {
        "200": {"content": {"application/json": {"schema": obj(result={"type": "string"})}}}
    }}}}}
    models, methods = extract_models_and_methods(spec, "Example")
    by_name = {model.name: model for model in models}
    assert len(by_name) == len(models)
    assert by_name["A"].fields[0].type != by_name["AB"].fields[0].type
    assert by_name[methods[0].return_type].fields[0].name == "result"
    assert by_name["Plain"].fields[0].type == "Optional[str]"
