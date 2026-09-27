"""Import boundaries for the repository package."""

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


def test_execution_repository_import_does_not_load_unrelated_repositories() -> None:
    result = _run_import_probe(
        """
        import json
        import sys

        from src.repositories import ExecutionRepository as public_repository
        from src.repositories import create_execution as public_create_execution
        from src.repositories.executions import ExecutionRepository
        from src.repositories.executions import create_execution

        print(json.dumps({
            "repository_identity": public_repository is ExecutionRepository,
            "create_identity": public_create_execution is create_execution,
            "unrelated_loaded": sorted(
                name for name in sys.modules
                if name in {
                    "src.repositories.config",
                    "src.repositories.knowledge",
                    "src.repositories.users",
                }
            ),
        }))
        """
    )

    assert result == {
        "repository_identity": True,
        "create_identity": True,
        "unrelated_loaded": [],
    }
