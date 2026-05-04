# Deploy resilience plan — issue #171

**Branch:** `171-deploy-resilience`
**Tracking:** https://github.com/jackmusick/bifrost/issues/171

## Goal

Make every push-to-main rollout safe: zero failed user-visible requests, zero duplicate workflow executions, no white-screen tabs across deploys.

## Out of scope (explicit)

- POST idempotency keys
- Vercel migration for the client
- Scheduler HA (still singleton, `Recreate`)
- WebSocket reconnect redesign (verify-only)

## Workstreams and ordering

The work splits into independent workstreams. Recommended commit order is by impact and risk: A first (smallest, highest user-visible win), then C (fixes the bundle bug), B (fixes double-execution), D, E. Workstream F mirrors A–D into `../kubernetes` once each lands.

### Workstream A — API rolling deploy

**Files**
- `k8s/api/deployment.yaml`
- `api/src/routers/health.py` (verify, may need update)

**Changes**
1. `replicas: 2`
2. Add `strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }`
3. Add `terminationGracePeriodSeconds: 35` on the pod spec
4. Add `lifecycle.preStop` to the api container:
   ```yaml
   lifecycle:
     preStop:
       exec:
         command: ["sleep", "5"]
   ```
5. Update the uvicorn command to include `--timeout-graceful-shutdown 30`:
   ```yaml
   command: ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000",
             "--timeout-graceful-shutdown", "30"]
   ```
6. **Audit `/health`**: confirm it actually checks DB + RabbitMQ + Redis connectivity before returning 200. If it's a dumb 200, fix it. Readiness must mean ready.

**Tests**
- Manual: deploy this, push a no-op to main, watch `kubectl get pods -w`, confirm both pods stay healthy through the rollout
- Unit test on `/health`: mock each dep to be down, expect 503

**Risk**
- Bumping replicas means 2x DB connection pool usage — confirm Postgres `max_connections` headroom and per-pod pool size product is comfortable
- **Latent: alembic concurrency on scale events.** `init_container.py` runs `alembic upgrade head` on every pod start. Rolling updates are safe (`maxSurge: 1` serializes init), but cold-start (0→2) and simultaneous reschedules can run two `alembic upgrade head` in parallel against the same DB. Real migrations with `CREATE INDEX CONCURRENTLY` or non-idempotent data steps can deadlock. Fix is a Postgres advisory lock around the upgrade in `init_container.py` (~5 lines). Out of scope for this issue; track separately if it ever bites.

---

### Workstream B — Worker graceful drain

**Files**
- `api/src/jobs/rabbitmq.py` (BaseConsumer + BroadcastConsumer)
- `api/src/worker/main.py` (signal handler + drain orchestration)
- `k8s/worker/deployment.yaml`
- `api/tests/unit/jobs/test_rabbitmq_drain.py` (new)

**Design**

Today (`api/src/jobs/rabbitmq.py:166`) `stop()` closes the channel immediately, killing any in-flight `_process_message_with_ack` task mid-process. The ack fails and RabbitMQ requeues — the same workflow runs twice.

New shape:

```
SIGTERM
  → Worker.stop()
      → for each consumer: consumer.drain()
          → cancel consumer (await self._queue.cancel(consumer_tag))   ← new deliveries stop
          → await asyncio.gather(*self._inflight, return_exceptions=True)
              with timeout=DRAIN_DEADLINE (default 300s, env-tunable)
          → close channel + connection
  → close RabbitMQ pools, close DB
  → exit 0
```

**Implementation steps**

1. **Track in-flight tasks in `BaseConsumer`**:
   ```python
   def __init__(self, ...):
       ...
       self._inflight: set[asyncio.Task] = set()
       self._consumer_tag: str | None = None
       self._draining = False
   ```

2. **Capture consumer tag** at `start()`:
   ```python
   self._consumer_tag = await queue.consume(self._on_message)
   ```

3. **Reject new messages while draining** in `_on_message`:
   ```python
   async def _on_message(self, message: IncomingMessage) -> None:
       if self._draining:
           # consumer was cancelled but a message slipped through
           await message.nack(requeue=True)
           return
       task = asyncio.create_task(self._process_message_with_ack(message))
       self._inflight.add(task)
       task.add_done_callback(self._inflight.discard)
   ```

4. **Add `drain()` method**:
   ```python
   async def drain(self, deadline: float) -> None:
       """Stop new deliveries, wait on in-flight, then close."""
       self._draining = True
       if self._queue and self._consumer_tag:
           await self._queue.cancel(self._consumer_tag)
       if self._inflight:
           logger.info(f"Draining {len(self._inflight)} in-flight on {self.queue_name}")
           try:
               await asyncio.wait_for(
                   asyncio.gather(*self._inflight, return_exceptions=True),
                   timeout=deadline,
               )
           except asyncio.TimeoutError:
               logger.warning(
                   f"Drain deadline exceeded with {len(self._inflight)} tasks still running on {self.queue_name}"
               )
       await self.stop()
   ```

5. **Worker.stop() calls drain() instead of stop() per consumer.**

6. **Hook into Redis heartbeat** so `/diagnostics` shows worker state. Find the heartbeat publisher in `api/src/jobs/` (process pool heartbeat, used by `api/src/routers/platform/workers.py`) and add a `state` field that flips to `"draining"` while drain is active.

7. **K8s manifest changes** in `k8s/worker/deployment.yaml`:
   - `terminationGracePeriodSeconds: 360`
   - `lifecycle.preStop: { exec: { command: ["sleep", "5"] } }`
   - `strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }`

**Tests**

Unit test (`api/tests/unit/jobs/test_rabbitmq_drain.py`):
- Mock consumer with a slow `process_message` (simulates 2s of work)
- Start consumer, dispatch one message, immediately call `drain()`
- Assert: drain blocks until message completes, ack is sent, no requeue
- Second test: dispatch two messages, drain with deadline=0.1s, assert one survives the deadline and gets warned-but-killed cleanly (no crash)

E2E (existing tests should keep passing — drain is a superset of current stop)

**Risk**
- The `await message.nack(requeue=True)` path in step 3 is rare (consumer cancel should be quick) but worth a unit test
- DRAIN_DEADLINE should match `terminationGracePeriodSeconds` minus a buffer (e.g., 300s drain, 360s grace, 60s for cleanup)

---

### Workstream C — Client version banner + bundle-error reload

**Files**
- `client/src/lib/version.ts` (existing — read-only, just import)
- `client/src/hooks/useVersionCheck.ts` (new)
- `client/src/components/layout/VersionUpdateBanner.tsx` (new)
- `client/src/main.tsx` (register `vite:preloadError` listener)
- `client/src/App.tsx` (mount the banner)
- `client/src/hooks/useVersionCheck.test.ts` (new)
- `k8s/client/deployment.yaml`

**Design**

`/api/version` already exists (`api/src/routers/version.py:14`) and returns `{version, min_cli_version}`. The hook polls it, compares to `APP_VERSION`, and exposes a boolean.

```ts
// useVersionCheck.ts
export function useVersionCheck(intervalMs = 60_000) {
  const [updateAvailable, setUpdateAvailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const check = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const res = await fetch("/api/version");
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && data.version !== APP_VERSION && APP_VERSION !== "unknown") {
          setUpdateAvailable(true);
        }
      } catch { /* ignore network errors */ }
    };

    const schedule = () => {
      timer = setTimeout(async () => {
        await check();
        if (!cancelled) schedule();
      }, intervalMs);
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") void check();
    };

    void check();          // immediate fire on mount
    schedule();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs]);

  return updateAvailable;
}
```

Banner component: non-dismissable toast at top of viewport, "A new version is available." + "Refresh" button → `window.location.reload()`. Use shadcn/ui primitives. Skip rendering when `APP_VERSION === "unknown"` (dev environment).

**Bundle-error reload** (in `main.tsx`):

```ts
window.addEventListener("vite:preloadError", () => {
  const lastReload = sessionStorage.getItem("bifrost:last-reload");
  const now = Date.now();
  if (lastReload && now - Number(lastReload) < 5_000) {
    // already reloaded within 5s — don't loop, surface the banner instead
    console.error("[bifrost] preload error after recent reload, suppressing");
    return;
  }
  sessionStorage.setItem("bifrost:last-reload", String(now));
  window.location.reload();
});
```

**K8s** (`k8s/client/deployment.yaml`):
- `replicas: 2`
- `strategy: { type: RollingUpdate, rollingUpdate: { maxSurge: 1, maxUnavailable: 0 } }`

**Tests**

Vitest (`useVersionCheck.test.ts`):
- Mock `fetch` to return matching version → `updateAvailable === false`
- Mock `fetch` to return different version → `updateAvailable === true`
- Mock `document.visibilityState = "hidden"` → no fetch fired
- Test sessionStorage loop guard with `vi.useFakeTimers`

Playwright optional: trigger banner manually by mocking `/api/version` response.

**Risk**
- `APP_VERSION` is "unknown" in dev (no `VITE_BIFROST_VERSION` set) — hook must skip the comparison or it'll banner constantly
- Polling adds ~1 RPS for 50 active users — confirm with backend team that this is fine (it is)

---

### Workstream D — Client retry on 502/503/504

**Files**
- `client/src/lib/api-client.ts`
- `client/src/lib/api-client.test.ts` (new or extend existing)

**Design**

Existing `apiClient` middleware already handles 401 (token refresh) and 429 (rate limit). Add a transient-5xx layer for idempotent methods.

```ts
const TRANSIENT_5XX = new Set([502, 503, 504]);
const IDEMPOTENT = new Set(["GET", "PUT", "DELETE", "HEAD", "OPTIONS"]);
const BACKOFF_MS = [250, 750, 2000];

async function withRetryOn5xx(request: Request, doFetch: () => Promise<Response>): Promise<Response> {
  if (!IDEMPOTENT.has(request.method)) return doFetch();

  let lastResponse: Response | null = null;
  for (let attempt = 0; attempt <= BACKOFF_MS.length; attempt++) {
    const response = await doFetch();
    if (!TRANSIENT_5XX.has(response.status)) return response;
    lastResponse = response;
    if (attempt < BACKOFF_MS.length) {
      await new Promise(r => setTimeout(r, BACKOFF_MS[attempt]));
    }
  }
  return lastResponse!;
}
```

Wire into the existing middleware chain. Order matters: 5xx retry should be **outside** the 401-refresh wrapper (refresh on 401, then bubble up; transient 5xx wraps the whole thing).

**Tests**
- GET that returns 503 twice then 200 → succeeds, 3 fetches
- POST that returns 503 → returns 503 immediately, 1 fetch (no retry)
- GET that returns 503 four times → returns 503, 4 fetches (3 retries exhausted)
- Retries don't apply to 401/429 (existing behavior preserved)

**Risk**
- A user clicking through quickly during a deploy could see compounded latency (up to 3s slower per failed request). Acceptable — the alternative is a visible error.

---

### Workstream E — SDK retry layer

**Files**
- Find the workflow SDK HTTP client. Likely candidates:
  - `api/shared/sdk/` or `api/src/services/workflow_sdk/`
  - Search for `httpx.AsyncClient` usage in workflow contexts

**Design**

Same shape as workstream D, different tuning:
- Backoff: 500ms / 1.5s / 4s / 10s / 20s (~36s total budget)
- Retry on 502/503/504 only
- Idempotent methods only (GET/PUT/DELETE)
- Use `httpx`'s built-in transport retry if available, else custom wrapper

**Tests**
- Unit tests with `respx` (httpx mock library) — same scenarios as D

**Risk**
- Workflow SDK calls can be in deeply nested code paths. The retry must be at the HTTP layer (transport), not per-call-site, so it's transparent to workflow authors.
- If the API is down for >36s, workflows fail — this is correct, but the failure message should mention "API was unavailable across X retries" for diagnosis.

**This workstream may be deferred to a follow-up PR if it grows.** It's the most exploratory of the five.

---

### Workstream F — Mirror to `../kubernetes` (live deployment)

**Files**
- `../kubernetes/components/bifrost/api/deployment.yaml`
- `../kubernetes/components/bifrost/worker/deployment.yaml`
- `../kubernetes/components/bifrost/client/deployment.yaml`
- `../kubernetes/components/bifrost/scheduler/deployment.yaml` (touch only if needed — likely no-op)

**Changes**

Apply the same diffs from workstreams A, B, C — verbatim where possible. Notes:
- The live `api/deployment.yaml` already has `replicas: 2`; just add the rollout strategy + preStop + graceful shutdown.
- Confirm the live worker `terminationGracePeriodSeconds` doesn't conflict with anything (e.g., Cloudflare tunnel timeouts).
- This is a separate repo — separate PR.

**Order:** ship F **after** the in-tree manifest changes are merged and dev has been observed for at least one push-to-main rollout. Don't blast prod with untested manifests.

---

## Sequencing for the PR(s)

**Single PR vs split:** A single PR for A + B + C + D in `bifrost`, then a follow-up PR (or commit on the same branch if quick) for E. F is its own PR in the `kubernetes` repo. This keeps the in-tree change reviewable in one pass while still letting F land separately when ready.

**Commit-by-commit within the PR:**
1. `feat(api): rolling-update strategy + uvicorn graceful shutdown` — workstream A
2. `feat(client): version-check banner + preload-error reload` — workstream C
3. `feat(client): retry transient 5xx on idempotent requests` — workstream D
4. `feat(worker): graceful drain on SIGTERM` — workstream B
5. `feat(sdk): retry transient 5xx on workflow API calls` — workstream E (or split)

Each commit independently passes `pyright`, `ruff`, `tsc`, `lint`, `./test.sh all`, `./test.sh client unit`.

## Verification (before opening PR)

Per CLAUDE.md's pre-completion checklist, in the worktree:

```bash
./debug.sh status | grep -q "Status:   UP" || ./debug.sh
cd api && pyright && ruff check .
cd ../client && npm run generate:types && npm run tsc && npm run lint
cd .. && ./test.sh stack up && ./test.sh all && ./test.sh client unit
```

Plus targeted manual verification:
- Boot the debug stack, open the app, confirm the version banner doesn't fire (versions match locally)
- `kubectl rollout restart deployment/bifrost-worker` against the dev cluster after merge → confirm no duplicate executions in `/diagnostics`

## Open questions parked for review

1. Should `useVersionCheck` poll interval be configurable per-env (e.g., 10s in dev, 60s in prod)? Default for now: hardcoded 60s, env-tunable later if needed.
2. Should the SDK retry budget include `Retry-After` honoring? Probably yes for 503; out of scope if the API never sends it (verify in workstream E).
3. Worker drain deadline (default 300s) — does this match the longest reasonable workflow? If workflows can run 30+ min, drain becomes useless and we need a different model (e.g., reject-and-requeue on shutdown). For now: assume <5min is the common case.
