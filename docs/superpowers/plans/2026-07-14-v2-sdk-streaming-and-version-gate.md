# v2 SDK Streaming Transport + Version Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the v2 web SDK's workflow hooks the v1 streaming engine (async execute + websocket + result fetch) so long-running workflows don't 504, and add a zero-maintenance SDK staleness gate (`bifrost solution sdk update` + a warning in `bifrost solution start`).

**Architecture:** The v2 SDK (`client/src/lib/app-sdk/`) keeps its public hook API (`useWorkflow`, `useWorkflowQuery`, `useWorkflowMutation`) but swaps the transport under `useWorkflow.run()`: POST `/api/workflows/execute` **without** `sync:true` → subscribe to the `execution:{id}` websocket channel (server support already exists, see `api/src/routers/websocket.py:795`) → stream logs/status → on terminal event, GET `/api/executions/{id}` and resolve with its `result`. The staleness gate reuses the existing tarball builder (`api/src/services/sdk_package/__init__.py`): stamp a sha256 content fingerprint of the built bundle into the tarball's `package.json`, report the current fingerprint in `GET /api/version`, compare in the CLI.

**Tech Stack:** TypeScript/React (vitest), Python/FastAPI/click (pytest), esbuild-built SDK tarball.

**GitHub issue:** #484. **Branch/worktree:** `.worktrees/484-v2-sdk-streaming-version-gate`, based off `origin/main` AFTER PR #483 merges (this work depends on #483's websocket message shape: terminal `execution_update` events carry `duration_ms` but NOT the result).

## Global Constraints

- Public hook APIs must not change shape in a breaking way: `useWorkflow(ref) → { data, loading, error, run }` may GAIN fields (`logs`, `status`, `executionId`) but existing fields keep their exact types.
- The transient short-circuit in `use-workflow.ts` (server returns `is_transient` + inline result for data providers) MUST be preserved — those never hit the websocket.
- No artificial client-side timeout on workflow runs. The server enforces the workflow's own `timeout_seconds`. (The old 5-minute ceiling was the bug.)
- Terminal statuses everywhere: `Success`, `Failed`, `CompletedWithErrors`, `Timeout`, `Cancelled`.
- The fingerprint must be a pure function of the built SDK bundle — no manual version bump anywhere. Human-readable `version` stays as-is (instance version, `_pep440ish`).
- `bifrost solution start` warning must NEVER block or exit — warn and continue.
- Python: `datetime.now(timezone.utc)` convention; every `except X: pass` needs an inline why-comment (CodeQL).
- CLI command surface changes require regenerating skill appendices: `python api/scripts/skill-truth/generate.py` and running `./test.sh tests/unit/test_cli_surface_smoke.py tests/unit/test_skill_appendix_fresh.py`.
- Run tests via `./test.sh <path>` (backend, needs `./test.sh stack up` once) and `cd client && npx vitest run <path>` (frontend).

---

### Task 1: `execution-stream.ts` — websocket subscription for one execution

**Files:**
- Create: `client/src/lib/app-sdk/execution-stream.ts`
- Test: `client/src/lib/app-sdk/execution-stream.test.ts`

**Interfaces:**
- Consumes: `buildWsUrl()` from `./ws-client` (already exported? — it IS exported: `export function buildWsUrl()`).
- Produces (Task 2 relies on these exact signatures):

```typescript
export interface ExecutionStreamEvent {
  type: "status" | "log";
  status?: string;            // on "status": Pending/Running/Success/Failed/...
  isTerminal?: boolean;       // on "status"
  log?: { level: string; message: string; timestamp: string; sequence?: number };
}
export function subscribeToExecution(
  executionId: string,
  onEvent: (evt: ExecutionStreamEvent) => void,
  onSocketDown?: () => void,  // fired on error/unexpected close — caller falls back to polling
): () => void                 // unsubscribe
```

The server sends frames on channel `execution:{id}` shaped (see `api/src/core/pubsub.py`):
- `{"type": "execution_update", "executionId": "<id>", "status": "Success", "duration_ms": 123}` — NO result field (post-#483).
- `{"type": "execution_log", "executionId": "<id>", "level": "info", "message": "...", "timestamp": "...", "sequence": 1}`

- [ ] **Step 1: Write the failing test.** Follow the existing `ws-client.test.ts` mock-WebSocket pattern (read it first; it stubs `global.WebSocket`). Test cases:

```typescript
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { subscribeToExecution } from "./execution-stream";

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  sent: string[] = [];
  listeners: Record<string, ((e: any) => void)[]> = {};
  constructor(url: string) { this.url = url; MockWebSocket.instances.push(this); }
  addEventListener(type: string, cb: (e: any) => void) { (this.listeners[type] ??= []).push(cb); }
  send(data: string) { this.sent.push(data); }
  close() { this.emit("close", { code: 1000 }); }
  emit(type: string, e: any) { (this.listeners[type] ?? []).forEach((cb) => cb(e)); }
}

beforeEach(() => { MockWebSocket.instances = []; vi.stubGlobal("WebSocket", MockWebSocket); });
afterEach(() => vi.unstubAllGlobals());

describe("subscribeToExecution", () => {
  it("subscribes to the execution channel on open", () => {
    subscribeToExecution("abc-123", () => {});
    const ws = MockWebSocket.instances[0];
    ws.emit("open", {});
    expect(JSON.parse(ws.sent[0])).toEqual({
      type: "subscribe",
      channels: [{ name: "execution:abc-123" }],
    });
  });

  it("maps execution_update frames to status events with terminal detection", () => {
    const events: any[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("open", {});
    ws.emit("message", { data: JSON.stringify({ type: "execution_update", executionId: "abc-123", status: "Running" }) });
    ws.emit("message", { data: JSON.stringify({ type: "execution_update", executionId: "abc-123", status: "Success", duration_ms: 42 }) });
    expect(events).toEqual([
      { type: "status", status: "Running", isTerminal: false },
      { type: "status", status: "Success", isTerminal: true },
    ]);
  });

  it("maps execution_log frames to log events", () => {
    const events: any[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("open", {});
    ws.emit("message", { data: JSON.stringify({ type: "execution_log", executionId: "abc-123", level: "info", message: "hi", timestamp: "t", sequence: 3 }) });
    expect(events[0]).toEqual({ type: "log", log: { level: "info", message: "hi", timestamp: "t", sequence: 3 } });
  });

  it("fires onSocketDown on error and on unexpected close, but not on unsubscribe", () => {
    const down = vi.fn();
    const unsub = subscribeToExecution("abc-123", () => {}, down);
    const ws = MockWebSocket.instances[0];
    ws.emit("error", {});
    expect(down).toHaveBeenCalledTimes(1);
    unsub(); // client-initiated close must NOT fire onSocketDown again
    expect(down).toHaveBeenCalledTimes(1);
  });

  it("ignores unparseable frames", () => {
    const events: any[] = [];
    subscribeToExecution("abc-123", (e) => events.push(e));
    const ws = MockWebSocket.instances[0];
    ws.emit("message", { data: "not json" });
    expect(events).toEqual([]);
  });
});
```

- [ ] **Step 2: Run to verify failure.** `cd client && npx vitest run src/lib/app-sdk/execution-stream.test.ts` — expect FAIL (module not found).

- [ ] **Step 3: Implement** `client/src/lib/app-sdk/execution-stream.ts`:

```typescript
/**
 * Live subscription to one execution's status + log stream over the SDK
 * websocket. Mirrors `subscribeToTable` in ws-client.ts: one socket per
 * subscription, auth via buildWsUrl() (token query param under a provider
 * transport, cookies same-origin).
 *
 * Terminal `execution_update` frames carry status + duration_ms but NOT the
 * result (large results are not rebroadcast; see #483) — the caller fetches
 * `/api/executions/{id}` when isTerminal fires.
 */
import { buildWsUrl } from "./ws-client";

const TERMINAL_STATUSES = new Set([
  "Success",
  "Failed",
  "CompletedWithErrors",
  "Timeout",
  "Cancelled",
]);

export interface ExecutionStreamEvent {
  type: "status" | "log";
  status?: string;
  isTerminal?: boolean;
  log?: { level: string; message: string; timestamp: string; sequence?: number };
}

export function subscribeToExecution(
  executionId: string,
  onEvent: (evt: ExecutionStreamEvent) => void,
  onSocketDown?: () => void,
): () => void {
  const ws = new WebSocket(buildWsUrl());
  let closedByClient = false;
  ws.addEventListener("open", () => {
    ws.send(
      JSON.stringify({
        type: "subscribe",
        channels: [{ name: `execution:${executionId}` }],
      }),
    );
  });
  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "execution_update" && msg.executionId === executionId) {
        onEvent({
          type: "status",
          status: msg.status,
          isTerminal: TERMINAL_STATUSES.has(msg.status),
        });
      } else if (msg.type === "execution_log" && msg.executionId === executionId) {
        onEvent({
          type: "log",
          log: {
            level: msg.level,
            message: msg.message,
            timestamp: msg.timestamp,
            sequence: msg.sequence,
          },
        });
      }
    } catch {
      // ignore unparseable frames — same policy as subscribeToTable
    }
  });
  ws.addEventListener("error", () => {
    console.warn("[bifrost-sdk] execution stream socket error");
    if (!closedByClient) onSocketDown?.();
  });
  ws.addEventListener("close", (e) => {
    if (!closedByClient) {
      console.warn("[bifrost-sdk] execution stream closed", e.code);
      onSocketDown?.();
    }
  });
  return () => {
    closedByClient = true;
    ws.close();
  };
}
```

NOTE the error+close double-fire: a socket error is typically followed by a close. Guard in the CALLER (Task 2 uses a `fellBack` flag) OR dedupe here — implement dedupe here: add `let downFired = false;` and in both handlers `if (!closedByClient && !downFired) { downFired = true; onSocketDown?.(); }`. The test in Step 1 (`error then unsubscribe → 1 call`) plus an added case (`error then close → still 1 call`) pin this.

- [ ] **Step 4: Run to verify pass.** Same command — expect all PASS.

- [ ] **Step 5: Commit.** `git add client/src/lib/app-sdk/execution-stream.{ts,test.ts} && git commit -m "feat(sdk): execution status/log stream subscription"`

---

### Task 2: `use-workflow.ts` — swap sync-hold for streaming transport

**Files:**
- Modify: `client/src/lib/app-sdk/use-workflow.ts`
- Test: `client/src/lib/app-sdk/use-workflow.test.tsx` (extend existing)

**Interfaces:**
- Consumes: `subscribeToExecution`, `ExecutionStreamEvent` (Task 1); `useBifrostContext()` → `{ authedFetch, appId }` (existing).
- Produces (Task 3 passes these through):

```typescript
export interface WorkflowLogEntry { level: string; message: string; timestamp: string; sequence?: number }
export interface UseWorkflowState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  run: (input?: Record<string, unknown>) => Promise<T>;
  logs: WorkflowLogEntry[];        // NEW — live log stream for the latest run
  status: string | null;           // NEW — latest run's status
  executionId: string | null;      // NEW — latest run's id
}
```

**Execution flow of the new `run()` (the current one is at `use-workflow.ts:59-104`, sends `sync: true`):**
1. POST `/api/workflows/execute` body `{ workflow_id, input_data, app_id? }` — **no `sync` key**.
2. Response body: `{ execution_id, status, result?, error?, is_transient? }`. If `is_transient` OR the returned `status` is already terminal → resolve/reject inline exactly like today (data providers + races). On `Failed`-family status throw `Error(body.error ?? ...)`.
3. Otherwise: subscribe via `subscribeToExecution(execution_id, ...)`.
4. Immediately after subscribing, GET `/api/executions/{execution_id}` once — fast workflows can finish before the socket opens; if terminal, settle now (unsubscribe first).
5. On stream `status` events: update `status` state. On `log` events: append to `logs` state (only if this run is still the latest, seq-guard).
6. On `isTerminal`: unsubscribe, GET `/api/executions/{execution_id}`, resolve with `.result` on `Success`/`CompletedWithErrors`, otherwise reject with `.error_message`.
7. `onSocketDown`: switch to polling GET `/api/executions/{execution_id}` every 2000ms until terminal (clear interval on settle). No overall deadline.
8. All hook-state writes stay behind the existing `seq === seqRef.current` guard; every run resets `logs` to `[]` and `status` to `"Pending"`.

- [ ] **Step 1: Write the failing tests.** Read `use-workflow.test.tsx` first and keep its harness (it wraps hooks in `<BifrostProvider>` with a mocked fetch). Mock `subscribeToExecution` with `vi.mock("./execution-stream", ...)`. New/changed cases:

```typescript
// Sketch of the new cases — adapt to the file's existing render/act helpers.
import { subscribeToExecution } from "./execution-stream";
vi.mock("./execution-stream", () => ({ subscribeToExecution: vi.fn() }));

it("executes async (no sync flag) and resolves with the fetched result on terminal event", async () => {
  // fetch mock: POST /api/workflows/execute → { execution_id: "e1", status: "Pending" }
  //             GET /api/executions/e1 (immediate check) → { status: "Running" }
  //             GET /api/executions/e1 (after terminal)  → { status: "Success", result: { ok: 1 } }
  let streamCb: any;
  (subscribeToExecution as Mock).mockImplementation((_id, cb) => { streamCb = cb; return vi.fn(); });
  const { result } = renderUseWorkflow("wf.py::main");
  let p: Promise<unknown>;
  act(() => { p = result.current.run({}); });
  await waitFor(() => expect(subscribeToExecution).toHaveBeenCalledWith("e1", expect.any(Function), expect.any(Function)));
  act(() => streamCb({ type: "status", status: "Success", isTerminal: true }));
  await expect(p!).resolves.toEqual({ ok: 1 });
  expect(result.current.data).toEqual({ ok: 1 });
  // assert the execute POST body had NO sync key:
  const executeBody = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect("sync" in executeBody).toBe(false);
});

it("streams logs into state", async () => { /* emit {type:"log"} events; expect result.current.logs to accumulate */ });

it("keeps the transient short-circuit", async () => {
  // POST → { execution_id: "e2", is_transient: true, status: "Success", result: { x: 2 } }
  // run() resolves { x: 2 } and subscribeToExecution is NEVER called.
});

it("settles from the immediate check when the run finished before the socket", async () => {
  // immediate GET returns { status: "Success", result: { fast: true } } → resolve without any stream event.
});

it("falls back to polling when the socket drops", async () => {
  vi.useFakeTimers();
  // capture onSocketDown (3rd arg), invoke it; GET returns Running, then Success with result.
  // advance 2000ms per poll; expect resolution.
});

it("rejects with error_message on Failed terminal", async () => { /* terminal event → GET returns { status: "Failed", error_message: "boom" } → run() rejects "boom" */ });
```

- [ ] **Step 2: Run to verify the new cases fail.** `npx vitest run src/lib/app-sdk/use-workflow.test.tsx` — new cases FAIL; pre-existing sync-transport cases will also fail once implementation changes (update them in step 3 to the new transport shape — the ones asserting `sync: true` in the POST body get inverted).

- [ ] **Step 3: Implement the new `run()`** in `use-workflow.ts` per the flow above. Concrete skeleton (preserve the module's existing doc comment style and the `seqRef` guard):

```typescript
const TERMINAL = new Set(["Success", "Failed", "CompletedWithErrors", "Timeout", "Cancelled"]);

const run = useCallback(
  async (input: Record<string, unknown> = {}): Promise<T> => {
    const seq = ++seqRef.current;
    setLoading(true);
    setError(null);
    if (seq === seqRef.current) { setLogs([]); setStatus("Pending"); }

    const settleFromExecution = (exec: any): T => {
      if (exec.status === "Success" || exec.status === "CompletedWithErrors") {
        return exec.result as T;
      }
      throw new Error(exec.error_message ?? `Workflow ${exec.status}`);
    };
    const fetchExecution = async (id: string) => {
      const r = await authedFetch(`/api/executions/${id}`);
      if (!r.ok) throw new Error(`failed to fetch execution: ${r.status}`);
      return r.json();
    };

    try {
      const resp = await authedFetch("/api/workflows/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workflow_id: workflowRef,
          input_data: input,
          ...(appId ? { app_id: appId } : {}),
        }),
      });
      if (!resp.ok) throw new Error(`workflow execution failed: ${resp.status} ${resp.statusText}`);
      const body = (await resp.json()) as ExecuteResponse;
      const execId = body.execution_id;
      if (seq === seqRef.current && execId) setExecutionId(execId);

      // Transient (data providers) and already-terminal responses settle inline.
      if (body.is_transient || TERMINAL.has(body.status)) {
        if (body.error || body.status === "Failed") {
          throw new Error(body.error ?? `Workflow failed (status: ${body.status})`);
        }
        const result = body.result as T;
        if (seq === seqRef.current) { setData(result); setStatus(body.status); }
        return result;
      }

      const result = await new Promise<T>((resolve, reject) => {
        let settled = false;
        let pollTimer: ReturnType<typeof setInterval> | null = null;
        let unsubscribe: () => void = () => {};
        const settle = (fn: () => void) => {
          if (settled) return;
          settled = true;
          if (pollTimer) clearInterval(pollTimer);
          unsubscribe();
          fn();
        };
        const checkOnce = async () => {
          try {
            const exec = await fetchExecution(execId);
            if (seq === seqRef.current && exec.status) setStatus(exec.status);
            if (TERMINAL.has(exec.status)) {
              settle(() => { try { resolve(settleFromExecution(exec)); } catch (e) { reject(e); } });
            }
          } catch {
            // transient fetch failure — the stream/poll keeps driving; a
            // persistent failure surfaces on the next terminal fetch attempt
          }
        };
        unsubscribe = subscribeToExecution(
          execId,
          (evt) => {
            if (evt.type === "log" && evt.log && seq === seqRef.current) {
              setLogs((prev) => [...prev, evt.log!]);
            }
            if (evt.type === "status") {
              if (seq === seqRef.current && evt.status) setStatus(evt.status);
              if (evt.isTerminal) void checkOnce();
            }
          },
          () => {
            // Socket died — degrade to polling. No deadline: the server
            // enforces the workflow's own timeout and will flip it terminal.
            if (!settled && !pollTimer) pollTimer = setInterval(checkOnce, 2000);
          },
        );
        void checkOnce(); // fast runs can finish before the socket opens
      });

      if (seq === seqRef.current) setData(result);
      return result;
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      if (seq === seqRef.current) setError(err);
      throw err;
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  },
  [authedFetch, workflowRef, appId],
);
```

Add the three new state hooks (`logs`, `status`, `executionId`) and return them. Update the module doc comment: it currently documents the sync transport; describe the streaming transport and that terminal ws frames don't carry results.

- [ ] **Step 4: Run the full file.** `npx vitest run src/lib/app-sdk/use-workflow.test.tsx` — all PASS.

- [ ] **Step 5: Typecheck + neighboring tests.** `npx tsc --noEmit && npx vitest run src/lib/app-sdk/` — all PASS.

- [ ] **Step 6: Commit.** `git commit -am "feat(sdk): streaming workflow transport — async execute + ws + result fetch"`

---

### Task 3: `use-workflow-hooks.ts` — pass through logs/status/executionId

**Files:**
- Modify: `client/src/lib/app-sdk/use-workflow-hooks.ts`
- Test: `client/src/lib/app-sdk/use-workflow-hooks.test.tsx` (extend)

**Interfaces:**
- Consumes: `UseWorkflowState<T>` from Task 2 (now has `logs`, `status`, `executionId`).
- Produces: `UseWorkflowQueryState<T>` and `UseWorkflowMutationState<T>` each gain `logs: WorkflowLogEntry[]`, `status: string | null`, `executionId: string | null` — same names, straight pass-through.

- [ ] **Step 1: Write failing tests** asserting `useWorkflowQuery` and `useWorkflowMutation` expose `logs`/`status`/`executionId` from the underlying `useWorkflow` (mock `./use-workflow` and return a canned state; assert pass-through).
- [ ] **Step 2: Verify fail**, **Step 3: implement** (add the three fields to both interfaces and both return objects), **Step 4: verify pass** (`npx vitest run src/lib/app-sdk/use-workflow-hooks.test.tsx`), **Step 5: commit** `git commit -am "feat(sdk): expose logs/status/executionId on query+mutation hooks"`.

---

### Task 4: SDK content fingerprint — tarball stamp + `/api/version`

**Files:**
- Modify: `api/src/services/sdk_package/__init__.py`
- Modify: `api/src/routers/version.py`
- Test: `api/tests/unit/test_sdk_package_fingerprint.py` (create)

**Interfaces:**
- Produces (Tasks 5+6 rely on these):
  - `sdk_fingerprint(version: str) -> str` — sha256 hex (first 16 chars) of the built bundle bytes; exported from `src.services.sdk_package`.
  - `sdk_contract_version() -> int` — reads `client/src/lib/app-sdk/sdk-contract.json` (new file, see below); exported from `src.services.sdk_package`.
  - Tarball `package/package.json` gains `"bifrost": {"fingerprint": "<16-hex>", "contract": <int>}`.
  - `GET /api/version` response gains `sdk_fingerprint: str` and `sdk_contract_version: int` (existing fields `version`, `contract_version` unchanged).

**Two-tier staleness model (mirrors the CLI's `CONTRACT_VERSION` + DTO-fingerprint pair):**
- `sdk-contract.json` — `{"version": 1}` plus a comment-style `"history"` map documenting each bump, exactly like the header comments in `api/bifrost/contract_version.py`. Bumped ONLY on breaking SDK↔server changes (the streaming transport in this very PR is bump #1: an SDK built before it expects `result` in terminal ws frames... actually pre-existing v2 SDKs used sync HTTP and still work — decide at implementation time whether this PR is a bump; document the decision in the history map either way).
- The content fingerprint changes on EVERY shipped SDK change (automatic); the contract version changes only on decided breaking changes (manual, tripwire-forced).
- Vitest tripwire (`client/src/lib/app-sdk/sdk-contract.test.ts`): snapshot-hash the SDK's wire-facing surface — the endpoints it calls, the request bodies it sends, the ws frame fields it reads (maintain these as an explicit exported const in a `wire-surface.ts` or extract from the modules). When the surface hash changes, the test fails with instructions: "refresh the snapshot if non-breaking, bump sdk-contract.json version if breaking" — the same forced-decision mechanism as `api/tests/unit/test_contract_version.py`.

Current builder (read it first): `build_sdk_tarball(version)` at `api/src/services/sdk_package/__init__.py:73` — lru_cached, bundles via `_bundle(workdir)`, writes `package_json` dict. The fingerprint is sha256 of the bundle bytes — a pure function of the SDK source baked into the image, so it changes exactly when the shipped SDK changes and never needs a manual bump.

- [ ] **Step 1: Write failing tests:**

```python
"""The SDK staleness gate hinges on the tarball fingerprint being a stable,
content-addressed stamp: same source -> same fingerprint, present both in the
tarball's package.json and in GET /api/version."""

import io
import json
import tarfile


def test_tarball_package_json_carries_fingerprint():
    from src.services.sdk_package import build_sdk_tarball, sdk_fingerprint

    data = build_sdk_tarball("v1.2.3")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        pkg = json.load(tar.extractfile("package/package.json"))

    assert pkg["bifrost"]["fingerprint"] == sdk_fingerprint("v1.2.3")
    assert len(pkg["bifrost"]["fingerprint"]) == 16


def test_fingerprint_is_stable_across_calls():
    from src.services.sdk_package import sdk_fingerprint

    assert sdk_fingerprint("v1.2.3") == sdk_fingerprint("v1.2.3")


def test_version_endpoint_reports_sdk_fingerprint(monkeypatch):
    from src.routers import version as version_router

    monkeypatch.setattr(version_router, "get_sdk_fingerprint", lambda: "abcd1234abcd1234")
    resp = version_router.VersionResponse  # shape check via model fields
    assert "sdk_fingerprint" in resp.model_fields
```

(Adapt the endpoint test to the router's actual structure after reading `api/src/routers/version.py` — it's ~20 lines; if the handler is a plain function, call it directly and assert the field.)

- [ ] **Step 2: Verify fail.** `./test.sh tests/unit/test_sdk_package_fingerprint.py` — FAIL (no `sdk_fingerprint`).

- [ ] **Step 3: Implement.** In `sdk_package/__init__.py`: extract the bundle build so fingerprint and tarball share it:

```python
import hashlib


@functools.lru_cache(maxsize=2)
def _built_bundle(version: str) -> bytes:
    """esbuild output for the SDK source baked into this image. Cached: pure
    function of the source; `version` keys the cache for rolling upgrades."""
    with tempfile.TemporaryDirectory(prefix="bifrost-sdk-build-") as tmp:
        return _bundle(Path(tmp))


def sdk_fingerprint(version: str) -> str:
    """Content fingerprint of the shipped SDK bundle (sha256, 16 hex chars).

    Pure function of the SDK source: changes exactly when the built SDK
    changes. This is what `bifrost solution start` compares against an app's
    installed copy — no manual bump anywhere.
    """
    return hashlib.sha256(_built_bundle(version)).hexdigest()[:16]
```

Refactor `build_sdk_tarball` to call `_built_bundle(version)` instead of `_bundle` directly (keep its own lru_cache), and add to `package_json`:

```python
            "bifrost": {"fingerprint": sdk_fingerprint(version)},
```

In `api/src/routers/version.py`: add `sdk_fingerprint: str` to `VersionResponse` and populate it. The instance version passed to the SDK builder elsewhere comes from the same source the version endpoint reports — read how `cli.py:2744` calls `build_sdk_tarball` and use the identical version source so the fingerprints agree:

```python
from src.services.sdk_package import sdk_fingerprint
# in the handler:
    sdk_fingerprint=sdk_fingerprint(<same version value used at the /api/sdk/download call site>),
```

NOTE: `sdk_fingerprint` shells out to node/esbuild on first call — acceptable (lru_cached, and /api/version is not hot), but wrap in try/except returning `"unavailable"` on failure so a broken node toolchain can't take down the version endpoint; log the exception.

- [ ] **Step 4: Verify pass.** `./test.sh tests/unit/test_sdk_package_fingerprint.py` — PASS. Also run the existing SDK package tests: `./test.sh tests/unit/test_sdk_*.py`.
- [ ] **Step 5: Commit.** `git commit -am "feat(api): content fingerprint on SDK tarball + /api/version"`

---

### Task 5: `bifrost solution sdk update` — re-vendor the SDK into a v2 app

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_solution_sdk_update.py` (create)

**Interfaces:**
- Consumes: server `GET /api/version` → `sdk_fingerprint` (Task 4); existing helpers in `solution.py`: `_SDK_DOWNLOAD_SUFFIX = "/api/sdk/download"` (line ~2822), `_heal_sdk_dep(app_dir, api_url)` (line ~2825), the `solution_group` click group (line 72), and however existing commands resolve the bound instance + app dir (read `solution start`'s resolution: `chosen.app_dir`, `client.api_url` around line 2293).
- Produces:
  - CLI: `bifrost solution sdk update [PATH]` — click sub-GROUP `sdk` under `solution_group` with an `update` command, so future sdk-related commands nest cleanly.
  - Pure helper (unit-testable, no network): `installed_sdk_fingerprint(app_dir: Path) -> str | None` — reads `<app_dir>/node_modules/bifrost/package.json`, returns `["bifrost"]["fingerprint"]`, `None` if missing/unreadable.

Behavior of `update`:
1. Resolve the app dir + bound instance exactly like `solution start` does (reuse its resolution helpers — do NOT reimplement).
2. GET `/api/version`; read `sdk_fingerprint`. If the server predates the field, say so and continue (update still works — it just can't verify).
3. Compare with `installed_sdk_fingerprint(app_dir)`. If equal, print "SDK already up to date (<fp>)" and exit 0.
4. Refresh: delete `<app_dir>/node_modules/bifrost` and the npm cache entry for the tarball URL is busted by installing with `--no-cache`? npm has no `--no-cache` install flag — the reliable sequence is `npm cache clean --force --silent` scoped is NOT possible, so instead: `rm -rf node_modules/bifrost` then `npm install bifrost@<api_url>/api/sdk/download --no-save=false`. npm treats a URL dep with the same URL as cached — bust it by running `npm install --force bifrost@<url>`. Use `--force` and verify afterwards by re-reading `installed_sdk_fingerprint` and comparing to the server's; if they still differ, exit 1 with a loud error naming both fingerprints.
5. Print the old → new fingerprint on success.

- [ ] **Step 1: Write failing unit tests** (follow `api/tests/unit/test_solution_dev_command.py`'s patterns — CliRunner + tmp_path app dirs + mocked HTTP):

```python
import json
from pathlib import Path


def _write_installed_sdk(app_dir: Path, fingerprint: str | None) -> None:
    pkg_dir = app_dir / "node_modules" / "bifrost"
    pkg_dir.mkdir(parents=True)
    pkg = {"name": "bifrost", "version": "1.0.0"}
    if fingerprint:
        pkg["bifrost"] = {"fingerprint": fingerprint}
    (pkg_dir / "package.json").write_text(json.dumps(pkg))


def test_installed_sdk_fingerprint_reads_stamp(tmp_path):
    from bifrost.commands.solution import installed_sdk_fingerprint

    _write_installed_sdk(tmp_path, "abcd1234abcd1234")
    assert installed_sdk_fingerprint(tmp_path) == "abcd1234abcd1234"


def test_installed_sdk_fingerprint_none_when_missing(tmp_path):
    from bifrost.commands.solution import installed_sdk_fingerprint

    assert installed_sdk_fingerprint(tmp_path) is None          # no node_modules
    _write_installed_sdk(tmp_path, None)                        # unstamped (old SDK)
    assert installed_sdk_fingerprint(tmp_path) is None


def test_sdk_update_skips_when_current(tmp_path, ...):
    # mock server /api/version -> {"sdk_fingerprint": "abcd1234abcd1234"}
    # installed fingerprint identical -> command exits 0, npm NOT invoked
    ...


def test_sdk_update_reinstalls_and_verifies(tmp_path, ...):
    # installed "old", server "new"; mock subprocess npm install; after install,
    # re-stamp the fake node_modules with "new" -> exits 0, prints old -> new
    ...


def test_sdk_update_fails_loud_when_still_stale(tmp_path, ...):
    # npm install mocked as no-op; fingerprints still differ -> exit code 1
    ...
```

Fill the `...` fixtures by copying the client/instance mocking approach used in `test_solution_dev_command.py` (read it; it has established fixtures for a bound workspace + fake API client).

- [ ] **Step 2: Verify fail.** `./test.sh tests/unit/test_solution_sdk_update.py`
- [ ] **Step 3: Implement** the `sdk` group + `update` command + `installed_sdk_fingerprint` in `api/bifrost/commands/solution.py`, reusing `solution start`'s app-dir/binding resolution and `_SDK_DOWNLOAD_SUFFIX`. Subprocess calls follow the file's existing npm-invocation style (it already runs npm for `solution start`).
- [ ] **Step 4: Verify pass**, plus the CLI surface tripwires: `./test.sh tests/unit/test_solution_sdk_update.py tests/unit/test_cli_surface_smoke.py tests/unit/test_skill_appendix_fresh.py`. If the appendix test fails: `python api/scripts/skill-truth/generate.py`, commit the regenerated files.
- [ ] **Step 5: Commit.** `git commit -am "feat(cli): bifrost solution sdk update"`

---

### Task 6: staleness warning in `bifrost solution start`

**Files:**
- Modify: `api/bifrost/commands/solution.py` (the `start` command path, near `_heal_sdk_dep` usage at line ~2293)
- Test: `api/tests/unit/test_solution_start_sdk_warning.py` (create)

**Interfaces:**
- Consumes: `installed_sdk_fingerprint(app_dir)` and `installed_sdk_contract(app_dir)` (Task 5 — the latter reads `["bifrost"]["contract"]`, `None` if absent), server `sdk_fingerprint` + `sdk_contract_version` via `GET /api/version` (Task 4).
- Produces: a pure helper returning the warning text or None — the `start` flow calls it and `click.secho`s the result (yellow for update-available, red for breaking):

```python
def sdk_staleness_warning(
    installed_fp: str | None,
    server_fp: str | None,
    installed_contract: int | None,
    server_contract: int | None,
) -> tuple[str, str] | None:  # (message, severity: "warn"|"breaking") or None
```

Rules (tiered — "no change" is silent, "non-breaking" is gentle, "breaking" is loud):
- `server_fp is None` (old server / fetch failed) → None (no warning, no noise).
- Fingerprints equal → None. **Nothing changed → never prompts.**
- Contract versions both present and DIFFERENT → `("Web SDK is incompatible with this server (SDK contract v<a>, server v<b>) — hooks may fail. Run: bifrost solution sdk update", "breaking")`.
- Fingerprints differ, contracts equal (or either contract missing) → `("Web SDK update available (non-breaking). Run: bifrost solution sdk update", "warn")`.
- `installed_fp is None` (unstamped old SDK) with `server_fp` present → the non-breaking "update available" message (can't tell more without a stamp).
- The warning must never raise and never block startup — wrap the version fetch in try/except (with a why-comment) and treat failure as `server_fp=None`.

- [ ] **Step 1: Write failing tests** for the four rule branches of `sdk_staleness_warning`, plus one CliRunner test asserting `solution start` prints the warning text (mock the version fetch + installed stamp) and STILL proceeds to boot (assert the next startup step is reached — follow how `test_solution_start_env.py` fakes the start sequence).
- [ ] **Step 2: Verify fail.** `./test.sh tests/unit/test_solution_start_sdk_warning.py`
- [ ] **Step 3: Implement** the helper + wire into `start` right after the SDK dep heal/install step (so the freshly-installed copy is what gets compared).
- [ ] **Step 4: Verify pass**, plus re-run `./test.sh tests/unit/test_solution_dev_command.py tests/unit/test_solution_start_env.py` (nearest neighbors).
- [ ] **Step 5: Commit.** `git commit -am "feat(cli): warn on stale web SDK in solution start"`

---

### Task 7: full verification + live drive (NOT subagent-delegable — run in the main session)

**Files:** none (verification only)

- [ ] Full local gates: `./test.sh unit`, `cd client && npx tsc --noEmit && npm run lint && npx vitest run`, `ruff check api/src api/bifrost`, pyright on touched files.
- [ ] Boot the worktree debug stack (`bifrost-debug` skill), scaffold or reuse a v2 solution app bound to it, and drive as a real user:
  - Short workflow (<5s): result appears, logs streamed live into the hook state.
  - Long workflow (`time.sleep(360)`, 6 min — PAST the old 5-minute nginx ceiling): no 504, run resolves. This is the headline fix; do not skip it.
  - Kill the API's websocket mid-run (restart api container): poll fallback resolves the run.
  - Stale-SDK flow: install SDK, bump SDK source (touch a comment in `client/src/lib/app-sdk/index.v2.ts` inside the stack), restart api, `bifrost solution start` → warning prints; `bifrost solution sdk update` → refreshes; warning gone.
- [ ] `python api/scripts/skill-truth/generate.py` if not already run; `bash scripts/sync-codex-skills.sh` if any skill files changed.
- [ ] Commit any stragglers; push; open PR with `Fixes #484`.

---

## Self-Review Notes

- Spec coverage: streaming transport (Tasks 1–3), fingerprint + version endpoint (Task 4), update command (Task 5), start warning (Task 6), debug-stack drive incl. >5-min run (Task 7). ✔
- Type consistency: `ExecutionStreamEvent`/`subscribeToExecution` (T1) consumed verbatim in T2; `UseWorkflowState` additions (T2) passed through in T3; `sdk_fingerprint`/`installed_sdk_fingerprint` names consistent across T4–T6. ✔
- Known unknowns called out inline rather than hidden: exact `version.py` handler shape (Task 4 step 1), `test_solution_dev_command.py` fixture reuse (Task 5 step 1), npm cache-bust behavior (Task 5 — verified-by-fingerprint regardless of npm's caching mood). Implementers must READ the named files before coding.
