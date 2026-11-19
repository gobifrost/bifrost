"""The decorators, errors and ``ai`` exported by ``bifrost`` in each mode.

Inside the platform, ``bifrost`` re-exports the ``src.sdk`` decorators so the
engine recognizes them, and the platform re-exports the SDK's error classes. The
standalone SDK has no ``src`` package: it exports its own decorators at runtime,
and type checkers must see those same objects.
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import bifrost

SDK_ROOT = Path(bifrost.__file__).resolve().parent

DECORATORS = ("workflow", "data_provider", "tool", "service")
ERRORS = ("UserError", "WorkflowError", "ValidationError", "IntegrationError", "ConfigurationError")


def _standalone_sdk(tmp_path: Path) -> Path:
    """Copy the SDK where no ``src`` package is importable, as a user install has it."""
    shutil.copytree(SDK_ROOT, tmp_path / "bifrost", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def _without_pythonpath() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}


def test_platform_mode_exports_platform_objects():
    from src.sdk import decorators, errors

    for name in DECORATORS:
        assert getattr(bifrost, name) is getattr(decorators, name), name
    for name in ERRORS:
        assert getattr(bifrost, name) is getattr(errors, name), name


def test_standalone_mode_exports_local_fallbacks(tmp_path):
    root = _standalone_sdk(tmp_path)
    code = textwrap.dedent(f"""
        import importlib.util
        assert importlib.util.find_spec("src") is None
        import bifrost, bifrost.decorators
        assert bifrost.__file__.startswith({str(root)!r} + "/"), bifrost.__file__
        for name in {DECORATORS!r}:
            assert getattr(bifrost, name) is getattr(bifrost.decorators, name), name
        from bifrost._errors import UserError, WorkflowError
        assert bifrost.UserError is UserError and bifrost.WorkflowError is WorkflowError
        for name in {ERRORS[2:]!r}:
            assert issubclass(getattr(bifrost, name), WorkflowError), name
    """)

    # -E ignores PYTHONPATH; unlike -I it keeps the working directory importable.
    subprocess.run([sys.executable, "-E", "-s", "-c", code], cwd=root, check=True)


def test_standalone_sdk_exports_type_check(tmp_path):
    root = _standalone_sdk(tmp_path)
    (root / "consumer.py").write_text(textwrap.dedent("""
        from bifrost import UserError, ValidationError, WorkflowError, ai, workflow

        reveal_type(UserError)
        reveal_type(ai)


        @workflow(name="probe")
        async def probe() -> None:
            await ai.complete("hello")
            raise UserError("shown to the user")


        def validation_failure() -> WorkflowError:
            return ValidationError("invalid input")


        workflow(name=123)  # invalid: the only expected diagnostic
    """))
    (root / "pyrightconfig.json").write_text(json.dumps({"include": ["consumer.py"]}))

    result = subprocess.run(
        ["pyright", "--outputjson", "--pythonpath", sys.executable],
        cwd=root, capture_output=True, text=True, env=_without_pythonpath(),
    )
    report = json.loads(result.stdout)
    diagnostics = report["generalDiagnostics"]
    revealed = [d["message"] for d in diagnostics if d["severity"] == "information"]
    problems = [
        (d["range"]["start"]["line"] + 1, d.get("rule")) for d in diagnostics if d["severity"] != "information"
    ]

    assert revealed == ['Type of "UserError" is "type[UserError]"', 'Type of "ai" is "type[ai]"']
    assert problems == [(18, "reportArgumentType")]
