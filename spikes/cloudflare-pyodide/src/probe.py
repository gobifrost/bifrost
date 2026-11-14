"""Reuse upstream execute, explicitly adapt only its settings import."""
import asyncio
from dataclasses import asdict
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor"))
import settings_adapter
sys.modules["src.config"] = settings_adapter

from src.services.execution.engine import ExecutionRequest, execute
from bifrost._execution_context import Caller
from fixtures import WORKLOADS


async def run(name, parameters=None, token=None):
    from bifrost.client import BifrostClient, _set_client, _clear_client
    started = time.perf_counter()
    # Only the isolated local API is reachable through this SDK injection.
    client = None
    if token:
        client = BifrostClient("http://host.docker.internal:39046", token)
        _set_client(client)
    request = ExecutionRequest(
        execution_id="00000000-0000-0000-0000-000000000001",
        caller=Caller("00000000-0000-0000-0000-000000000002", "probe@example.invalid", "Probe"),
        organization=None, func=WORKLOADS[name], name=name,
        parameters=parameters or {}, transient=True, no_cache=True,
        engine_token=token,
    )
    try:
        result = await asyncio.wait_for(execute(request), timeout=0.05 if name.endswith("timeout") else 20)
        data = asdict(result)
    except Exception as exc:
        data = {"status": "HarnessError", "error_type": type(exc).__name__, "error_message": str(exc)}
    finally:
        _clear_client()
        if client:
            await client.close()
    data["probe_wall_ms"] = (time.perf_counter()-started)*1000
    return data
