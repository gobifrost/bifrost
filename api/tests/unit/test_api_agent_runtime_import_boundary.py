"""The API process does not import the agent runtime.

Physical agent compute executes in worker children. API orchestration loads
its runtime only when needed; importing the API application and reconciling
an idle worker must not eagerly load Pydantic AI or the agent executors.
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
import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import src.main  # noqa: F401
from src.worker.remote.host import ApiExecutor, Bridge
from src.worker.remote.ownership import RemoteOwnerLease

async def reconcile_idle_worker():
    lease = RemoteOwnerLease(
        worker_id=uuid4(), owner_id=uuid4(), launcher_id=uuid4(), session_generation=1,
    )
    bridge = Bridge(Mock(), {{
        "current": 1, "limit": 100, "hostname": "runner", "requirements": {{}},
    }}, lease)
    bridge.pool = Mock(reconcile_inventory=AsyncMock(return_value={{}}))
    bridge.inventory = {{"routes": [], "agent_compute_stages": []}}
    bridge.inventory_ready.set()
    await ApiExecutor(bridge, lease.worker_id)._reconcile_transport_inventory()

asyncio.run(reconcile_idle_worker())

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
