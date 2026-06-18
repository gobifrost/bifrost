"""Unit tests for the solution-deploy workflow preflight.

``preflight_workflows`` flags only the genuinely execution-breaking case: a
bundle entry whose ``function_name`` is not defined anywhere in the carried
source (the "Executable not found" class). A manifest ``name`` that differs from
the decorated or function name is NOT an error — execution resolves a workflow
by its ``function_name`` (service.py / module_loader.py), so the DB ``name`` is
identity/display only.
"""
from src.services.solution_deploy_preflight import preflight_workflows


def test_no_errors_when_function_exists():
    wfs = [
        {
            "name": "Sandbox Ticket Snapshot",
            "function_name": "main",
            "path": "workflows/snap.py",
            "source": '@workflow(name="Sandbox Ticket Snapshot")\ndef main():\n    pass\n',
        }
    ]
    assert preflight_workflows(wfs) == []


def test_slug_differs_from_decorator_is_not_an_error():
    # Manifest slug "hello" diverges from the decorated name — import resolves
    # this to the decorated name, so preflight must NOT block it.
    wfs = [
        {
            "name": "hello",
            "function_name": "main",
            "path": "workflows/snap.py",
            "source": '@workflow(name="Sandbox Ticket Snapshot")\ndef main():\n    pass\n',
        }
    ]
    assert preflight_workflows(wfs) == []


def test_slug_differs_from_bare_function_is_not_an_error():
    # Bare function (no @workflow decorator), slug "main" != function "run".
    # The function exists, so this is a legitimate, non-blocking bundle.
    wfs = [
        {
            "name": "main",
            "function_name": "run",
            "path": "workflows/main.py",
            "source": "def run(sdk):\n    return 'ok'\n",
        }
    ]
    assert preflight_workflows(wfs) == []


def test_reports_missing_function_with_guidance():
    wfs = [
        {
            "name": "hello",
            "function_name": "main",
            "path": "workflows/snap.py",
            # Source defines `other`, not `main`.
            "source": '@workflow(name="X")\ndef other():\n    pass\n',
        }
    ]
    errors = preflight_workflows(wfs)
    assert len(errors) == 1
    msg = errors[0]
    assert "main" in msg
    assert "workflows/snap.py" in msg
    assert "hello" in msg


def test_no_source_is_skipped():
    wfs = [{"name": "hello", "function_name": "main", "path": "workflows/snap.py"}]
    assert preflight_workflows(wfs) == []


def test_unparseable_source_is_not_blocked():
    # Static verification is impossible; preflight defers to the engine.
    wfs = [
        {
            "name": "hello",
            "function_name": "main",
            "path": "workflows/snap.py",
            "source": "def def (:::",
        }
    ]
    assert preflight_workflows(wfs) == []
