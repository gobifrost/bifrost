"""Fixed workflow fixtures shared unchanged by Pyodide and Linux benchmarks."""
import asyncio
import logging
from bifrost import workflow, integrations, tables, context


@workflow
async def json_transform(size: int = 1000):
    rows = [{"id": i, "value": i * 3} for i in range(size)]
    return {"count": len(rows), "sum": sum(row["value"] for row in rows)}


@workflow
async def async_context(value: int = 7):
    await asyncio.sleep(0)
    return {"value": value, "execution_id": context.execution_id}


@workflow
async def workspace_import():
    from workspace_helper import transform
    return transform(6)


@workflow
async def http_orchestration():
    import httpx
    async with httpx.AsyncClient() as client:
        replies = await asyncio.gather(*[client.get("https://example.com") for _ in range(3)])
    return {"statuses": [reply.status_code for reply in replies]}


@workflow
async def integration_read():
    data = await integrations.get("pyodide-spike")
    return {"found": data is not None, "config": data.config if data else None}


@workflow
async def table_update():
    doc = await tables.upsert("pyodide-spike", id="probe", data={"value": 42})
    read = await tables.get("pyodide-spike", "probe")
    return {"written": doc.data, "read": read.data}


@workflow
async def multi_step(fail: bool = False):
    print("stdout probe")
    logging.getLogger(__name__).warning("structured log probe")
    result = await json_transform(10)
    await asyncio.sleep(0)
    if fail:
        raise ValueError("intentional failure")
    return result


def sync_json():
    return {"sync": True}


@workflow
async def state_write():
    import builtins
    builtins._pyodide_spike_marker = "synthetic-execution-A"
    return True


@workflow
async def state_read():
    import builtins
    return getattr(builtins, "_pyodide_spike_marker", None)


@workflow
async def io_timeout():
    await asyncio.sleep(2)
    return True


@workflow
async def cpu_timeout():
    import time
    deadline = time.perf_counter() + 0.15
    while time.perf_counter() < deadline:
        pass
    return "CPU completed before timeout callback could run"


@workflow
async def secret_probe():
    from bifrost._context import register_secret
    register_secret("synthetic-secret-12345")
    print("synthetic-secret-12345")
    logging.getLogger(__name__).warning("synthetic-secret-12345")
    raise ValueError("synthetic-secret-12345")


@workflow
async def memory_probe():
    block = bytearray(160 * 1024 * 1024)
    block[-1] = 1
    return {"allocated_bytes": len(block)}


@workflow
async def duplicate_probe():
    import builtins
    builtins._pyodide_spike_duplicate = getattr(builtins, "_pyodide_spike_duplicate", 0) + 1
    return builtins._pyodide_spike_duplicate


@workflow
async def termination_probe():
    await asyncio.sleep(10)
    return True


@workflow
async def sync_http_probe():
    import httpx
    try:
        response = httpx.get("https://example.com", timeout=3)
        return {"status": response.status_code}
    except Exception as exc:
        return {"error_type": type(exc).__name__, "error": str(exc)}


WORKLOADS = {f.__name__: f for f in (
    json_transform, async_context, workspace_import, http_orchestration, integration_read,
    table_update, multi_step, sync_json, state_write, state_read, io_timeout, cpu_timeout, secret_probe,
    memory_probe, duplicate_probe, termination_probe, sync_http_probe
)}
