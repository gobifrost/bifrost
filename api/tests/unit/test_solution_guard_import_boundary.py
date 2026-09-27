"""Import boundaries for solution write guards."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[2]


def _run_import_probe(source: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_install_solution_write_guard_does_not_import_fastapi() -> None:
    result = _run_import_probe(
        """
        import json
        import sys

        from src.services.solutions.guard import install_solution_write_guard

        install_solution_write_guard()

        print(json.dumps({
            "fastapi_loaded": any(
                name == "fastapi" or name.startswith("fastapi.")
                for name in sys.modules
            ),
            "guard_installed": getattr(install_solution_write_guard, "_installed", False),
        }))
        """
    )

    assert result == {
        "fastapi_loaded": False,
        "guard_installed": True,
    }


def test_solution_managed_http_guard_still_raises_409() -> None:
    result = _run_import_probe(
        """
        import json
        import sys
        from types import SimpleNamespace

        from src.services.solutions.guard import (
            SOLUTION_MANAGED_MESSAGE,
            assert_not_solution_managed,
        )

        try:
            assert_not_solution_managed(SimpleNamespace(solution_id="managed"))
        except Exception as exc:
            payload = {
                "exception_type": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
                "detail": getattr(exc, "detail", None),
                "fastapi_loaded": any(
                    name == "fastapi" or name.startswith("fastapi.")
                    for name in sys.modules
                ),
            }
        else:
            payload = {"exception_type": None}

        payload["expected_detail"] = SOLUTION_MANAGED_MESSAGE
        print(json.dumps(payload))
        """
    )

    assert result == {
        "exception_type": "HTTPException",
        "status_code": 409,
        "detail": "Solution-managed entities can only be managed by deployment methods.",
        "fastapi_loaded": True,
        "expected_detail": (
            "Solution-managed entities can only be managed by deployment methods."
        ),
    }
