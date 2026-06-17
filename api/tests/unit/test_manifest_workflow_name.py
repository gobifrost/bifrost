"""Unit tests: the @workflow decorator name is the execution identity.

Manifest import must write the *decorator* name into ``Workflow.name`` (the
value ``service.py`` matches at execution time), never the manifest dict slug.
"""
from src.services.solution_deploy_preflight import extract_workflow_name_from_source


def test_extracts_decorator_name():
    src = '@workflow(name="Sandbox Ticket Snapshot")\ndef main():\n    pass\n'
    assert extract_workflow_name_from_source(src, "main") == "Sandbox Ticket Snapshot"


def test_defaults_to_function_name_when_no_name_kwarg():
    src = "@workflow()\ndef main():\n    pass\n"
    assert extract_workflow_name_from_source(src, "main") == "main"


def test_defaults_to_function_name_for_bare_decorator():
    src = "@workflow\ndef main():\n    pass\n"
    assert extract_workflow_name_from_source(src, "main") == "main"


def test_handles_attribute_decorator():
    src = '@bifrost.workflow(name="Snap")\ndef main():\n    pass\n'
    assert extract_workflow_name_from_source(src, "main") == "Snap"


def test_targets_named_function_only():
    src = (
        '@workflow(name="First")\n'
        "def first():\n    pass\n\n"
        '@workflow(name="Second")\n'
        "def second():\n    pass\n"
    )
    assert extract_workflow_name_from_source(src, "second") == "Second"


def test_unparseable_source_returns_none():
    assert extract_workflow_name_from_source("def def (:::", "main") is None


def test_missing_function_returns_function_name():
    src = '@workflow(name="X")\ndef other():\n    pass\n'
    assert extract_workflow_name_from_source(src, "main") == "main"
