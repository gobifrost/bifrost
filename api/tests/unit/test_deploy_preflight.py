"""Unit tests for the solution-deploy workflow-name preflight.

``preflight_workflows`` catches a bundle whose manifest entry name diverges
from the decorated ``@workflow(name=...)`` in the carried source, returning a
human-readable error per mismatch (empty list = OK).
"""
from src.services.solution_deploy_preflight import preflight_workflows


def test_no_errors_when_names_match():
    wfs = [
        {
            "name": "Sandbox Ticket Snapshot",
            "function_name": "main",
            "path": "workflows/snap.py",
            "source": '@workflow(name="Sandbox Ticket Snapshot")\ndef main():\n    pass\n',
        }
    ]
    assert preflight_workflows(wfs) == []


def test_reports_mismatch_with_guidance():
    wfs = [
        {
            "name": "hello",
            "function_name": "main",
            "path": "workflows/snap.py",
            "source": '@workflow(name="Sandbox Ticket Snapshot")\ndef main():\n    pass\n',
        }
    ]
    errors = preflight_workflows(wfs)
    assert len(errors) == 1
    msg = errors[0]
    assert "hello" in msg
    assert "Sandbox Ticket Snapshot" in msg
    assert "workflows/snap.py" in msg
    assert "main" in msg


def test_no_source_is_skipped():
    wfs = [{"name": "hello", "function_name": "main", "path": "workflows/snap.py"}]
    assert preflight_workflows(wfs) == []


def test_default_function_name_decorator_matches_entry_name():
    # @workflow() with no name → decorator name is the function name; an entry
    # whose name equals the function name is a match, not a mismatch.
    wfs = [
        {
            "name": "main",
            "function_name": "main",
            "path": "workflows/snap.py",
            "source": "@workflow()\ndef main():\n    pass\n",
        }
    ]
    assert preflight_workflows(wfs) == []
