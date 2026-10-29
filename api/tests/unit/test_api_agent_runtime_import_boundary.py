"""The API process does not import the agent runtime.

Agent runs and chat turns execute on workers, in children of the launcher's
agent template. Importing the API application must not load the runtime
those children import (Pydantic AI, CodeMode, the agent executors).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]

AGENT_RUNTIME_MODULES = (
    "pydantic_ai",
    "pydantic_ai_harness",
    "src.services.agent_executor",
    "src.services.agent_runtime",
    "src.services.execution.autonomous_agent_executor",
    "src.services.llm.pydantic_client",
    "src.jobs.consumers.agent_run",
)


def test_importing_the_api_application_loads_no_agent_runtime() -> None:
    code = f"""
import json
import sys

import src.main  # noqa: F401

print(json.dumps(sorted(
    name for name in sys.modules
    if any(name == module or name.startswith(module + ".") for module in {AGENT_RUNTIME_MODULES!r})
)))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.splitlines()[-1]) == []
