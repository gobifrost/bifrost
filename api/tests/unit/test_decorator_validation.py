import inspect

import pytest

from bifrost.decorator_validation import _SUPPORTED_KEYS, validate_python_decorators
from bifrost.definition_validation import DefinitionValidationError


@pytest.mark.parametrize(
    "source",
    [
        "from bifrost import workflow\n@workflow(id='stable', name=dynamic_name)\ndef run(): pass\n",
        "from bifrost import tool as task\n@task(description=build_description())\ndef run(): pass\n",
        "import bifrost as bf\n@bf.data_provider(category='catalog')\ndef values(): pass\n",
        "import bifrost.decorators as decorators\n@decorators.service(tags=['listener'])\ndef bridge(): pass\n",
        "import bifrost.decorators\n@bifrost.decorators.workflow(name='run')\ndef run(): pass\n",
        "from bifrost import decorators as bf_decorators\n@bf_decorators.workflow(is_tool=True)\ndef run(): pass\n",
        "from bifrost.decorators import workflow as flow\n@flow(**{**{'name': make_name()}, 'id': 'stable'})\ndef run(): pass\n",
        "from bifrost import *\n@workflow(name='run')\ndef run(): pass\n",
        "from bifrost.decorators import *\n@service(name='bridge')\ndef bridge(): pass\n",
    ],
)
def test_accepts_supported_bifrost_decorator_settings(source):
    validate_python_decorators(source, path="workflows/example.py")


@pytest.mark.parametrize(
    "source",
    [
        "from bifrost import workflow\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from bifrost import tool\n@tool(**{'enabled': setting})\ndef run(): pass\n",
        "from bifrost import data_provider\n@data_provider(cache_ttl=5)\ndef values(): pass\n",
        "from bifrost import service\n@service(retry_policy={'max_attempts': 2})\ndef bridge(): pass\n",
        "from bifrost import *\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from bifrost.decorators import *\n@tool(enabled=True)\ndef run(): pass\n",
        "from bifrost import workflow\nflow = workflow\n@flow(timeout_seconds=10)\ndef run(): pass\n",
    ],
)
def test_rejects_unsupported_settings_without_evaluating_values(source):
    with pytest.raises(DefinitionValidationError) as exc_info:
        validate_python_decorators(source, path="workflows/example.py")

    issue = exc_info.value.issues[0]
    assert issue.path == "workflows/example.py"
    assert issue.code == "unsupported_decorator_setting"
    expected_line = next(
        number for number, line in enumerate(source.splitlines(), start=1)
        if line.startswith("@")
    )
    assert issue.line == expected_line


def test_rejects_dynamic_expansion_without_executing_source():
    source = (
        "from bifrost import workflow\n"
        "raise RuntimeError('must not run')\n"
        "@workflow(**options)\n"
        "def run(): pass\n"
    )

    with pytest.raises(DefinitionValidationError) as exc_info:
        validate_python_decorators(source, path="workflows/example.py")

    issue = exc_info.value.issues[0]
    assert issue.code == "dynamic_decorator_expansion"
    assert "literal mapping" in issue.message


@pytest.mark.parametrize(
    "source",
    [
        "def workflow(**kwargs): pass\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from bifrost import workflow\nworkflow = other\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from other import workflow\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from bifrost import workflow\nfrom other import *\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from bifrost import workflow as flow\nfrom other import *\n@flow(timeout_seconds=10)\ndef run(): pass\n",
        "import bifrost as bf\nfrom .other import *\n@bf.workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from .bifrost import workflow\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        "from .bifrost import *\n@workflow(timeout_seconds=10)\ndef run(): pass\n",
        (
            "from bifrost import workflow\n"
            "for workflow in decorators:\n"
            "    @workflow(timeout_seconds=10)\n"
            "    def run(): pass\n"
        ),
        (
            "from bifrost import workflow\n"
            "with manager() as workflow:\n"
            "    @workflow(timeout_seconds=10)\n"
            "    def run(): pass\n"
        ),
        (
            "from bifrost import workflow\n"
            "try:\n"
            "    pass\n"
            "except Exception as workflow:\n"
            "    @workflow(timeout_seconds=10)\n"
            "    def run(): pass\n"
        ),
    ],
)
def test_ignores_unrelated_or_shadowed_same_named_decorators(source):
    validate_python_decorators(source, path="workflows/example.py")


def test_reports_python_syntax_errors_with_source_location():
    with pytest.raises(DefinitionValidationError) as exc_info:
        validate_python_decorators("@workflow(\ndef run(): pass\n", path="workflows/example.py")

    issue = exc_info.value.issues[0]
    assert issue.code == "invalid_python"
    assert issue.line == 1
    assert "syntax" in issue.message.lower()


@pytest.mark.parametrize("module_name", ["bifrost.decorators", "src.sdk.decorators"])
def test_supported_keys_match_owned_decorator_signatures(module_name: str):
    """Keep static acceptance aligned with the trusted local/server decorators."""
    module = __import__(module_name, fromlist=["*"])
    for decorator, supported_keys in _SUPPORTED_KEYS.items():
        signature = inspect.signature(getattr(module, decorator))
        signature_keys = {
            parameter.name
            for parameter in signature.parameters.values()
            if not parameter.name.startswith("_")
            and parameter.kind is not inspect.Parameter.VAR_KEYWORD
        }
        assert supported_keys == signature_keys | {"id"}
