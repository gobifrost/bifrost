"""Pure agent-compute modules must stay free of API/ORM import side effects."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[4]
FORBIDDEN_MODULE_PREFIXES = (
    "src.config",
    "src.core.database",
    "src.models.orm",
    "src.services.ai_model_service",
    "sqlalchemy",
)


def test_pure_agent_compute_imports_no_api_or_orm_modules() -> None:
    code = f"""
import json
import sys

import src.services.agent_runtime.composition_view  # noqa: F401
import src.services.agent_runtime.compute  # noqa: F401
import src.worker.remote.agent_compute  # noqa: F401

print(json.dumps(sorted(
    name for name in sys.modules
    if any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in {FORBIDDEN_MODULE_PREFIXES!r}
    )
)))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        capture_output=True,
        env={},
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1]) == []
