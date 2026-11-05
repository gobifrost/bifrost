"""In-cluster benchmark driver; runs in the bench-driver pod on the Bifrost image.

    python - <mode> '<json args>'    (stdin = this file; prints one JSON line)

Modes:
- ``setup``: log in, register the bench workflows and tools, seed
  production-count workflows, create the bench table, configure the model
  backend and agents, set the worker policy. Idempotent.
- ``run_class``: run one execution class sequentially, then as a burst, and
  return the platform's own records (timestamps, status, error). Chat classes
  send messages the way the chat page does: ``chat-first`` opens a new chat,
  ``chat-followup`` times a second message in an existing one.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("BENCH_API_URL", "http://api:8000")
TERMINAL_WORKFLOW = {"Success", "Failed", "Timeout", "Stuck", "CompletedWithErrors", "Cancelled"}
ACTIVE_AGENT = {"queued", "pending", "running"}
AGENT_PROMPT = "Use the available tools to check the bench state, then reply briefly."

BENCH_SOURCE = '''import asyncio
import json

from bifrost import config, tables, tool, workflow


@workflow(name="bench_wf_noop", description="Benchmark class wf-noop: platform overhead only.")
async def bench_wf_noop() -> dict:
    return {"ok": True}


@workflow(name="bench_wf_sdk", description="Benchmark class wf-sdk: SDK round trips through the API.")
async def bench_wf_sdk() -> dict:
    values = [await config.get(f"bench_cfg_{index}", default=None) for index in range(1, 6)]
    page = await tables.query("bench_docs", limit=20)
    await tables.insert("bench_writes", {"configs": len(values)})
    return {"configs": len(values), "docs": len(page.documents)}


@workflow(name="bench_wf_wait", description="Benchmark class wf-wait: simulated external wait.")
async def bench_wf_wait() -> dict:
    await asyncio.sleep(2)
    return {"waited_s": 2}


@workflow(name="bench_wf_cpu", description="Benchmark class wf-cpu: about 20 MB JSON transform.")
async def bench_wf_cpu() -> dict:
    rows = [{"i": i, "name": f"device-{i:06d}", "tags": ["a", "b", "c"], "v": i * 1.5} for i in range(250_000)]
    blob = json.dumps(rows)
    back = json.loads(blob)
    return {"bytes": len(blob), "rows": len(back)}


@workflow(name="bench_wf_large", description="Benchmark class wf-large: about 5 MB result.")
async def bench_wf_large() -> dict:
    return {"blob": "x" * 5_000_000}


@tool(name="bench_tool_lookup", description="Look up the bench configuration value.")
async def bench_tool_lookup() -> dict:
    return {"value": await config.get("bench_cfg_1", default="none")}


@tool(name="bench_tool_count", description="Count a page of bench documents.")
async def bench_tool_count() -> dict:
    page = await tables.query("bench_docs", limit=5)
    return {"count": len(page.documents), "total": page.total}


@tool(name="bench_tool_note", description="Record a bench note.")
async def bench_tool_note() -> dict:
    await tables.insert("bench_notes", {"note": "bench"})
    return {"ok": True}
'''
BENCH_FUNCTIONS = (
    "bench_wf_noop", "bench_wf_sdk", "bench_wf_wait", "bench_wf_cpu", "bench_wf_large",
    "bench_tool_lookup", "bench_tool_count", "bench_tool_note",
)
WORKFLOW_CLASSES = {
    "wf-noop": "bench_wf_noop",
    "wf-sdk": "bench_wf_sdk",
    "wf-wait": "bench_wf_wait",
    "wf-cpu": "bench_wf_cpu",
    "wf-large": "bench_wf_large",
}
AGENT_CLASSES = {
    "agent-chat": ("bench_agent_chat", "delegated"),
    "agent-tools": ("bench_agent_tools", "delegated"),
    "agent-codemode": ("bench_agent_tools", "automatic"),
}
CHAT_CLASSES = {"chat-first", "chat-followup"}
CHAT_PROMPT = "Reply with one short sentence about the bench."
CHAT_AGENT = "bench_agent_chat"
BACKENDS = {
    "fake": {"endpoint": "http://bench-fake-llm:8080/v1", "model": "bench-fake", "key_env": None},
    "deepseek": {"endpoint": "https://api.deepseek.com", "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
}


async def client() -> httpx.AsyncClient:
    http = httpx.AsyncClient(base_url=BASE, timeout=300)
    login = await http.post(
        "/auth/login",
        data={"username": os.environ["BIFROST_DEFAULT_USER_EMAIL"], "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"]},
    )
    login.raise_for_status()
    http.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return http


def _check(response: httpx.Response, what: str) -> httpx.Response:
    if response.status_code >= 300:
        raise SystemExit(f"{what} failed: HTTP {response.status_code} {response.text[:400]}")
    return response


async def _workflow_ids(http: httpx.AsyncClient) -> dict[str, str]:
    listed = _check(await http.get("/api/workflows"), "list workflows").json()
    rows = listed if isinstance(listed, list) else listed.get("items", listed.get("workflows", []))
    return {row["name"]: row["id"] for row in rows}


async def _write_and_register(http: httpx.AsyncClient, path: str, source: str, functions: list[str], known: dict[str, str]) -> None:
    missing = [name for name in functions if name not in known]
    if not missing:
        return
    _check(await http.put("/api/files/editor/content", json={"path": path, "content": source, "encoding": "utf-8"}), f"write {path}")
    sem = asyncio.Semaphore(8)

    async def register(name: str) -> None:
        async with sem:
            response = await http.post("/api/workflows/register", json={"path": path, "function_name": name})
        if response.status_code >= 300 and response.status_code != 409:
            raise SystemExit(f"register {name} failed: HTTP {response.status_code} {response.text[:300]}")

    await asyncio.gather(*(register(name) for name in missing))


def _seed_source(kind: str, indices: range) -> tuple[str, list[str]]:
    names = [f"seed_{kind}_{index:04d}" for index in indices]
    body = "".join(
        f'''

@{kind}(name="{name}", description="Seeded {kind} {name}: reconcile site devices and report drift.")
async def {name}(organization_id: str = "", site_id: int = 0, device_filter: str = "all", include_offline: bool = False, max_items: int = 100) -> dict:
    """Seeded {kind}.

    Args:
        organization_id: Customer organization.
        site_id: Site scope.
        device_filter: Devices to include.
        include_offline: Whether offline devices count.
        max_items: Upper bound on processed items.
    """
    return {{"ok": True}}
'''
        for name in names
    )
    return f"from bifrost import {kind}\n" + body, names


async def _configure_backend(http: httpx.AsyncClient, backend: str) -> dict:
    spec = BACKENDS[backend]
    api_key = os.environ[spec["key_env"]] if spec["key_env"] else "bench-fake-key"
    name = f"bench-{backend}"
    connections = _check(await http.get("/api/admin/ai/connections"), "list connections").json()
    connection = next((c for c in connections if c["name"] == name), None)
    body = {"name": name, "provider": "openai_compatible", "api_key": api_key, "endpoint": spec["endpoint"]}
    if connection is None:
        connection = _check(await http.post("/api/admin/ai/connections", json=body), "create connection").json()
    else:
        _check(await http.patch(f"/api/admin/ai/connections/{connection['id']}", json=body), "update connection")
    profiles = _check(await http.get("/api/admin/ai/profiles"), "list profiles").json()
    profile = next((p for p in profiles if p["name"] == name), None)
    if profile is None:
        profile = _check(await http.post("/api/admin/ai/profiles", json={
            "name": name, "connection_id": connection["id"], "model": spec["model"], "enabled_for_chat": True,
        }), "create profile").json()
    for key in ("primary", "summarization", "chat_default"):
        _check(await http.put(f"/api/admin/ai/assignments/{key}", json={"profile_id": profile["id"]}), f"assign {key}")
    return {"connection_id": connection["id"], "profile_id": profile["id"], "model": spec["model"]}


async def _ensure_agents(http: httpx.AsyncClient, tool_ids: list[str]) -> None:
    listed = _check(await http.get("/api/agents"), "list agents").json()
    existing = {agent["name"] for agent in (listed if isinstance(listed, list) else listed.get("items", []))}
    agents = {
        "bench_agent_chat": [],
        "bench_agent_tools": tool_ids,
    }
    for name, tools in agents.items():
        if name in existing:
            continue
        _check(await http.post("/api/agents", json={
            "name": name,
            "system_prompt": "You are a benchmark agent. Use tools when they are available, then answer in one sentence.",
            "access_level": "authenticated",
            "channels": ["chat"],
            "tool_ids": tools,
            "max_iterations": 6,
        }), f"create agent {name}")


async def _ensure_table(http: httpx.AsyncClient, docs: int) -> None:
    created = await http.post("/api/tables", json={"name": "bench_docs"})
    if created.status_code >= 300 and created.status_code != 409 and "exist" not in created.text.lower():
        raise SystemExit(f"create table failed: HTTP {created.status_code} {created.text[:300]}")
    count = _check(await http.get("/api/tables/bench_docs/documents/count"), "count docs").json().get("count", 0)
    if count >= docs:
        return
    batch = [{"data": {"device": f"workstation-{i:05d}", "os": "Windows 11", "note": "x" * 1500}} for i in range(docs - count)]
    for start in range(0, len(batch), 500):
        _check(await http.post("/api/tables/bench_docs/documents/batch", json={"documents": batch[start:start + 500]}), "insert docs")


async def setup(seed: dict, backend: str | None, policy: dict, table_docs: int) -> dict:
    http = await client()
    try:
        _check(await http.put("/api/admin/kubernetes/policy", json=policy), "set worker policy")
        known = await _workflow_ids(http)
        await _write_and_register(http, "workflows/bench_classes.py", BENCH_SOURCE, list(BENCH_FUNCTIONS), known)
        for kind, count in seed.items():
            for start in range(0, count, 50):
                source, names = _seed_source(kind, range(start, min(start + 50, count)))
                await _write_and_register(http, f"workflows/seed_{kind}_{start:04d}.py", source, names, known)
        known = await _workflow_ids(http)
        await _ensure_table(http, table_docs)
        model = await _configure_backend(http, backend) if backend else None
        await _ensure_agents(http, [known[name] for name in ("bench_tool_lookup", "bench_tool_count", "bench_tool_note")])
        seeded = sum(1 for name in known if name.startswith("seed_"))
        return {"workflows": len(known), "seeded": seeded, "model": model}
    finally:
        await http.aclose()


POLL_INTERVAL_S = 1.0


async def _wait_all(http: httpx.AsyncClient, list_path: str, params: dict, ids: list[str], timeout_s: int) -> list[dict]:
    """Wait for runs with one list request per tick, so polling adds almost no load.

    Workflow executions and agent runs both expose created/started/completed
    timestamps on their list routes; those are what the stats use.
    """
    pending = set(ids)
    found: dict[str, dict] = {}
    deadline = time.monotonic() + timeout_s
    while pending and time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_S)
        listed = _check(await http.get(list_path, params=params), f"list {list_path}").json()
        rows = listed if isinstance(listed, list) else listed.get("executions") or listed.get("items") or []
        for row in rows:
            row_id = str(row.get("execution_id") or row.get("id"))
            if row_id in pending and _terminal(row):
                found[row_id] = row
                pending.discard(row_id)
    records = []
    for run_id in ids:
        row = found.get(run_id, {"status": "TimedOutInBench"})
        records.append({
            "id": run_id, "status": row.get("status"), "created_at": row.get("created_at"),
            "started_at": row.get("started_at"), "completed_at": row.get("completed_at"),
            "error": row.get("error_message") or row.get("error"),
            "iterations": row.get("iterations_used"), "tokens": row.get("tokens_used"),
        })
    return records


def _terminal(row: dict) -> bool:
    if "execution_id" in row:
        return row.get("status") in TERMINAL_WORKFLOW
    return row.get("status") not in ACTIVE_AGENT


async def run_class(name: str, sequential: int, burst: int, timeout_s: int, burst_rounds: int = 1) -> dict:
    http = await client()
    try:
        if name in WORKFLOW_CLASSES:
            workflow_id = (await _workflow_ids(http))[WORKFLOW_CLASSES[name]]
            list_path, params = "/api/executions", {"workflowId": workflow_id, "limit": 50}

            async def submit() -> str:
                body = {"workflow_id": workflow_id, "input_data": {}, "sync": False}
                return _check(await http.post("/api/workflows/execute", json=body), "execute").json()["execution_id"]
        elif name in CHAT_CLASSES:
            _check(await http.put("/api/admin/ai/behavior", json={"agent_execution_mode": "delegated"}), "set execution mode")
            listed = _check(await http.get("/api/agents"), "list agents").json()
            agents = listed if isinstance(listed, list) else listed.get("items", [])
            chat_agent_id = next(agent["id"] for agent in agents if agent["name"] == CHAT_AGENT)
            list_path, params = "/api/agent-runs", {"trigger_type": "chat", "limit": 50}

            async def send(conversation_id: str | None) -> dict:
                body = {"content": CHAT_PROMPT, "agent_id": chat_agent_id}
                if conversation_id:
                    body["conversation_id"] = conversation_id
                return _check(await http.post("/api/chat/runs", json=body), "send chat message").json()

            if name == "chat-first":
                async def submit() -> str:
                    # What the chat page does when it opens, then the first message.
                    _check(await http.get("/api/chat/model-profiles"), "load chat model profiles")
                    return str((await send(None))["run_id"])
            else:
                async def submit() -> str:
                    # An untimed opening message creates the conversation; the follow-up is timed.
                    opened = await send(None)
                    await _wait_all(http, list_path, params, [str(opened["run_id"])], timeout_s)
                    return str((await send(str(opened["conversation"]["id"])))["run_id"])
        else:
            agent_name, mode = AGENT_CLASSES[name]
            _check(await http.put("/api/admin/ai/behavior", json={"agent_execution_mode": mode}), "set execution mode")
            listed = _check(await http.get("/api/agents"), "list agents").json()
            agents = listed if isinstance(listed, list) else listed.get("items", [])
            agent_id = next(agent["id"] for agent in agents if agent["name"] == agent_name)
            list_path, params = "/api/agent-runs", {"agent_id": agent_id, "limit": 50}

            async def submit() -> str:
                body = {"agent_name": agent_name, "input": {"message": AGENT_PROMPT}}
                return str(_check(await http.post("/api/agent-runs/enqueue", json=body), "enqueue agent").json()["run_id"])

        sequential_records: list[dict] = []
        for _ in range(sequential):
            sequential_records += await _wait_all(http, list_path, params, [await submit()], timeout_s)
        burst_records: list[dict] = []
        for _ in range(burst_rounds):
            ids = list(await asyncio.gather(*(submit() for _ in range(burst))))
            burst_records += await _wait_all(http, list_path, params, ids, timeout_s)
        return {"class": name, "sequential": sequential_records, "burst": burst_records}
    finally:
        await http.aclose()


def main() -> None:
    mode, args = sys.argv[1], json.loads(sys.argv[2])
    handler = {"setup": setup, "run_class": run_class}[mode]
    print(json.dumps(asyncio.run(handler(**args))))


main()
