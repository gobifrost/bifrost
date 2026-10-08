# v1 Tables Session Renewal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an inline v1 app's `tables.*` / `useTable` request gets a 401 because the 30-minute session cookie lapsed, renew the session through the host platform and retry the request once, instead of failing with `tables: 401 {"detail":"Not authenticated"}`.

**Architecture:** The web data SDK's single request helper, `http()` in `client/src/lib/app-sdk/tables.ts`, gains a renew-and-retry-once step on 401 for the same-origin (v1, cookie) transport only. Renewal goes through the existing host bridge `globalThis.__BIFROST_PLATFORM_AUTH_V1__` (installed by `client/src/lib/api-client.ts`), the same single-flight refresh path that `BifrostProvider.authedFetch` already uses for v2 apps. The bridge accessor moves from `provider.tsx` into `transport.ts` so `tables.ts` and `provider.tsx` share one definition.

**Tech Stack:** TypeScript, React 19, Vitest + happy-dom, @testing-library/react.

**Spec:** No separate spec. Background below is the investigation record (Covi Halo ticket #419771, Braytel CRM).

## Background

- Platform sessions: `access_token` cookie `Max-Age=1800` (30 min), `csrf_token` cookie `Max-Age=1800`, `refresh_token` cookie 7 days (`api/src/routers/auth.py::set_auth_cookies`). `POST /api/auth/refresh` rotates all three.
- v1 inline apps never mount `<BifrostProvider>`, so `getBifrostTransport()` is the default `{ baseUrl: "" }`. `http()` then does a bare same-origin `fetch` with `credentials: "include"` and an `X-CSRF-Token` read from `document.cookie`. It has **no 401 handling**: any 401 throws `Error("tables: 401 <body>")`.
- Nothing in a v1 app renews the cookie. It only gets renewed when some other host code path (the platform `apiClient`, a websocket 4001 reconnect) happens to call `refreshAccessToken()`. Between cookie expiry and that next renewal, every table read and write fails.
- User-visible effect (Braytel CRM): saves vanish or toast `tables: 401 {"detail":"Not authenticated"}`. A newly mounted `useTable` (opening a location card) resolves to `rows: []` with `error` set. The app ignores `error`, so data shows as missing ("Unknown provider").
- Reproduced on a local debug stack: query/insert with a valid session → 200/201; with `access_token` cookie removed → 401 `{"detail":"Not authenticated"}`; after `POST /api/auth/refresh` (and re-reading the rotated `csrf_token`) → 200.
- v2 apps are unaffected: `BifrostProvider` installs `fetchImpl: authedFetch`, which already refreshes once via the bridge and retries (`provider.tsx`, covered by `provider.test.tsx` "refreshes once after a 401 and retries the request with the rotated token").

## Global Constraints

- Scope: `tables.ts` request path only. `files.ts` has the same pattern and the v1 websocket treats close code 4001 as non-retryable (`ws-client.ts:54`). Both are **out of scope** and listed as follow-ups in the PR description.
- Renewal applies **only** to the same-origin transport (`usingProvider === false`). The provider transport already renews in `authedFetch`, and a second renewal layer there would double-refresh.
- Retry **at most once** per request. Never loop.
- Use the host bridge (`__BIFROST_PLATFORM_AUTH_V1__`). Never import `@/lib/api-client` from `app-sdk/`. Standalone v2 apps ship their own SDK copy, and the bridge exists precisely to avoid that import.
- Honor `canRefreshAccessToken()`. Embed sessions return `false` and must not borrow the refresh cookie.
- Re-read `csrf_token` from `document.cookie` for the retry. Refresh rotates it, and the stale value yields `403 CSRF token mismatch`.
- On unrecoverable 401 (refresh fails, or retry still 401), call `handleAuthenticationFailure()` and then throw the same `tables: 401 …` error as today. This mirrors `authedFetch`.
- Repo rule (CLAUDE.md): modified `.ts` files under `client/src/lib/**` that export functions need sibling vitest coverage.
- Must pass `npm run tsc` and `npm run lint` in `client/`.
- Conventional commits; PR title < 70 chars (CONTRIBUTING.md).

## Review Focus

1. **Parallel 401 burst.** A v1 page mounts ~8–11 `useTable`s at once. All of them 401 together and all must recover. The bridge's `refreshAccessToken` is single-flight, so we expect one refresh and every request retried once → success (test in Task 2).
2. **Mutations retried after a stale-CSRF refresh.** A POST/PATCH/DELETE retry must carry the *rotated* CSRF token, not the original (test in Task 2).
3. **Retry result still goes through 403/404/204 handling.** For example, a retried `tables.get` that 404s must still return `null`, not throw (test in Task 2).
4. **Embed sessions / no bridge.** No refresh attempt, behavior identical to today (tests in Task 2).
5. **`useTable` recovers end-to-end.** The snapshot 401s, renewal succeeds, and the hook ends with rows populated and `error === null`. This is the exact Braytel symptom (test in Task 3).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `client/src/lib/app-sdk/transport.ts` | Modify | Add `PlatformAuthBridge` type + `getPlatformAuth()` accessor (module-global host state lives here already) |
| `client/src/lib/app-sdk/transport.test.ts` | Create | Coverage for `getPlatformAuth()` (repo rule) |
| `client/src/lib/app-sdk/provider.tsx` | Modify | Drop local `PlatformAuthBridge`/`platformAuth()`; import `getPlatformAuth` from `./transport` |
| `client/src/lib/app-sdk/tables.ts` | Modify | `http()`: renew-and-retry-once on 401 for same-origin transport |
| `client/src/lib/app-sdk/tables.test.ts` | Modify | 401 renewal tests |
| `client/src/lib/app-sdk/use-table.test.tsx` | Modify | End-to-end hook recovery test |

---

### Task 0: Worktree, toolchain, and the "before" browser baseline

All work happens in the worktree `~/Github/bifrost/.claude/worktrees/v1-tables-session-renewal` on branch `fix/v1-tables-session-renewal` (already created from `origin/main` @ `2e125fee8`). **Do not** use or tear down the primary checkout's debug stack (`bifrost-debug-7a226595`, port 36462). Its volumes date from 2026-05-05 and hold the user's local data.

- [ ] **Step 1: Confirm the worktree is current**

```bash
cd ~/Github/bifrost/.claude/worktrees/v1-tables-session-renewal
git fetch origin && git rebase origin/main   # no-op unless main moved
```

- [ ] **Step 2: Install client deps (host has no node_modules)**

```bash
cd client && npm ci
```

- [ ] **Step 3: Unit baseline. The touched suites must be green before changes**

Run: `cd client && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/tables.test.ts src/lib/app-sdk/use-table.test.tsx src/lib/app-sdk/provider.test.tsx`
Expected: all pass (39+ tests).

- [ ] **Step 4: Boot this worktree's own debug stack (fresh volumes, port mode)**

```bash
BIFROST_FORCE_PORT=1 ./debug.sh up
./debug.sh status          # record Open: URL as $URL (http://localhost:<port>)
```

Its client is Vite + HMR from this worktree's `client/src`, so the Task 2 change goes live on page reload with no restart.

- [ ] **Step 5: Seed a v1 repro app on that stack** (scratch CLI outside the repo, per CLAUDE.md)

```bash
SCR=/tmp/claude-1000/-home-michael-Github/d5858e91-3a49-4204-a199-c3a66ea19f33/scratchpad/wt-cli
mkdir -p $SCR && cd $SCR
python3 -m venv .venv && .venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet "$URL/api/cli/download/bifrost-cli.tar.gz"
.venv/bin/bifrost login --url "$URL" --email dev@gobifrost.com --password password
.venv/bin/bifrost tables create --name repro-providers
.venv/bin/bifrost apps create --name "Auth Repro" --slug auth-repro --access-level authenticated --app-model inline_v1
SRC=/tmp/claude-1000/-home-michael-Github/d5858e91-3a49-4204-a199-c3a66ea19f33/scratchpad/cli/src
.venv/bin/bifrost files write apps/auth-repro/_layout.tsx --from-file $SRC/_layout.tsx --create-only
.venv/bin/bifrost files write apps/auth-repro/pages/index.tsx --from-file $SRC/pages/index.tsx --create-only
.venv/bin/bifrost files list apps/auth-repro/pages/   # verify; CLI can fail silently on version warnings

# Seed the one provider row the cards look up (cookie login, same as the investigation repro)
J=$SCR/jar.txt
curl -s -c $J -o /dev/null -X POST "$URL/api/auth/login" -d 'username=dev@gobifrost.com&password=password'
CSRF=$(awk '$6=="csrf_token"{print $7}' $J)
curl -s -b $J -X POST "$URL/api/tables/repro-providers/documents" -H 'content-type: application/json' \
  -H "X-CSRF-Token: $CSRF" -d '{"data":{"slot":1,"name":"Vonage Business"}}'   # expect 201 JSON
```

The app source is the investigation's repro. Card 0 mounts a provider `useTable` at page load. "Open another card" mounts a **new** `useTable`, and the card renders `Unknown provider` when rows are empty (same as Braytel's `LocationServicesList`), with `error` shown beside it. "Save" calls `tables.insert` and prints `saved ok` or the error.

- [ ] **Step 6: Write the browser harness** (`$SCR/e2e/expiry.spec.ts`, scratch only, never committed)

```ts
import { test, expect } from "@playwright/test";

const URL = process.env.REPRO_URL!;

test("v1 table calls after the session cookie lapses", async ({ page, context }) => {
	await page.goto(`${URL}/login`);
	await page.getByLabel(/email/i).fill("dev@gobifrost.com");
	await page.getByLabel(/password/i).fill("password");
	await page.getByRole("button", { name: /sign in|log in|continue/i }).first().click();
	await page.waitForURL((u) => !u.pathname.startsWith("/login"));

	await page.goto(`${URL}/apps/auth-repro/preview`);
	await expect(page.getByTestId("card-0-name")).toHaveText("Vonage Business");

	// Simulate the 30-minute expiry: the browser drops access_token; the
	// refresh cookie and the host's localStorage token remain, exactly as in prod.
	await context.clearCookies({ name: "access_token" });

	const statuses: string[] = [];
	page.on("response", (r) => {
		const p = new URL(r.url()).pathname;
		if (p.startsWith("/api/tables/") || p === "/api/auth/refresh") statuses.push(`${r.status()} ${r.request().method()} ${p}`);
	});

	await page.getByTestId("open").click();
	await page.getByTestId("save").click();
	await page.waitForTimeout(3000);
	await page.screenshot({ path: `/out/${process.env.PHASE}.png`, fullPage: true });

	console.log(JSON.stringify({
		phase: process.env.PHASE,
		card1: await page.getByTestId("card-1-name").textContent(),
		card1Error: await page.getByTestId("card-1-error").textContent(),
		save: await page.getByTestId("save-msg").textContent(),
		statuses,
	}, null, 2));
});
```

Run it in the repo's Playwright image (the host lacks Chromium system libraries; `--network host` reaches the stack's localhost port):

```bash
docker run --rm --network host -e REPRO_URL="$URL" -e PHASE=before \
  -v $SCR/e2e:/work -v $SCR/out:/out -w /work \
  mcr.microsoft.com/playwright:v1.63.0-jammy \
  sh -c "npm i --silent @playwright/test@1.63.0 && npx playwright test expiry.spec.ts --reporter=line"
```

- [ ] **Step 7: Record the "before" result.** This must reproduce the bug before any code changes:
  - `card1` = `Unknown provider`, `card1Error` contains `tables: 401`
  - `save` = `tables: 401 {"detail":"Not authenticated"}`
  - `statuses` shows `401 POST /api/tables/repro-providers/documents/query` and **no** `/api/auth/refresh`
  - Screenshot saved at `$SCR/out/before.png`

If "before" does not reproduce, stop and report. The fix can't be shown to work against a harness that doesn't show the bug.

---

### Task 1: Share the platform auth bridge accessor

**Files:**
- Modify: `client/src/lib/app-sdk/transport.ts`
- Create: `client/src/lib/app-sdk/transport.test.ts`
- Modify: `client/src/lib/app-sdk/provider.tsx` (remove `PlatformAuthBridge`, `PlatformAuthGlobal`, `platformAuth()` at ~lines 76–89; replace call sites)

**Interfaces:**
- Produces: `export interface PlatformAuthBridge { getAccessToken(): string | null; canRefreshAccessToken(): boolean; refreshAccessToken(): Promise<boolean>; handleAuthenticationFailure(): void }` and `export function getPlatformAuth(): PlatformAuthBridge | undefined` from `./transport`.

- [ ] **Step 1: Write the failing test** (`transport.test.ts`)

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { getPlatformAuth, type PlatformAuthBridge } from "./transport";

type G = typeof globalThis & { __BIFROST_PLATFORM_AUTH_V1__?: PlatformAuthBridge };

afterEach(() => {
	delete (globalThis as G).__BIFROST_PLATFORM_AUTH_V1__;
});

describe("getPlatformAuth", () => {
	it("returns undefined when the host has not installed a bridge", () => {
		expect(getPlatformAuth()).toBeUndefined();
	});

	it("returns the bridge the host installed, read at call time", () => {
		const bridge: PlatformAuthBridge = {
			getAccessToken: () => "tok",
			canRefreshAccessToken: () => true,
			refreshAccessToken: vi.fn(async () => true),
			handleAuthenticationFailure: vi.fn(),
		};
		(globalThis as G).__BIFROST_PLATFORM_AUTH_V1__ = bridge;
		expect(getPlatformAuth()).toBe(bridge);
	});
});
```

- [ ] **Step 2: Run it. Expect FAIL** (`getPlatformAuth` is not exported)

Run: `cd client && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/transport.test.ts`

- [ ] **Step 3: Implement in `transport.ts`** (append below `getBifrostTransport`)

```ts
/**
 * Session bridge the host platform installs (`lib/api-client.ts`). Lets the
 * data SDK, including a standalone v2 app's own SDK copy, share the host's
 * live token and single-flight refresh without importing host internals.
 * Absent outside the platform document (tests, external embeds).
 */
export interface PlatformAuthBridge {
	getAccessToken: () => string | null;
	canRefreshAccessToken: () => boolean;
	refreshAccessToken: () => Promise<boolean>;
	handleAuthenticationFailure: () => void;
}

type PlatformAuthGlobal = typeof globalThis & {
	__BIFROST_PLATFORM_AUTH_V1__?: PlatformAuthBridge;
};

/** Read the host session bridge at call time (it is installed at host boot). */
export function getPlatformAuth(): PlatformAuthBridge | undefined {
	return (globalThis as PlatformAuthGlobal).__BIFROST_PLATFORM_AUTH_V1__;
}
```

- [ ] **Step 4: Point `provider.tsx` at it.** Delete its local `PlatformAuthBridge` interface, `PlatformAuthGlobal` type and `platformAuth()` function. Add `import { getPlatformAuth } from "./transport";`, then replace every `platformAuth()` call with `getPlatformAuth()`:

```bash
grep -n "platformAuth()" client/src/lib/app-sdk/provider.tsx   # expect 0 after edit
```

- [ ] **Step 5: Run tests. Expect PASS** (new test + unchanged provider suite)

Run: `cd client && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/transport.test.ts src/lib/app-sdk/provider.test.tsx`

- [ ] **Step 6: Commit**

```bash
git add client/src/lib/app-sdk/transport.ts client/src/lib/app-sdk/transport.test.ts client/src/lib/app-sdk/provider.tsx
git commit -m "refactor(app-sdk): share platform auth bridge accessor via transport"
```

---

### Task 2: Renew the session and retry once on 401 in `tables` `http()`

**Files:**
- Modify: `client/src/lib/app-sdk/tables.ts` (`http()`, ~lines 87–137)
- Test: `client/src/lib/app-sdk/tables.test.ts`

**Interfaces:**
- Consumes: `getPlatformAuth()` / `PlatformAuthBridge` from `./transport` (Task 1).
- Produces: no new exports. `tables.*` public behavior changes only on 401.

- [ ] **Step 1: Write the failing tests.** Append to `tables.test.ts`. Add `import type { PlatformAuthBridge } from "./transport";` at the top, and extend the existing top-level `afterEach` with `delete (globalThis as AuthGlobal).__BIFROST_PLATFORM_AUTH_V1__; document.cookie = "csrf_token=; max-age=0"; vi.unstubAllGlobals();`.

```ts
type AuthGlobal = typeof globalThis & { __BIFROST_PLATFORM_AUTH_V1__?: PlatformAuthBridge };

function installBridge(over: Partial<PlatformAuthBridge> = {}) {
	const bridge: PlatformAuthBridge = {
		getAccessToken: () => null,
		canRefreshAccessToken: () => true,
		refreshAccessToken: vi.fn(async () => true),
		handleAuthenticationFailure: vi.fn(),
		...over,
	};
	(globalThis as AuthGlobal).__BIFROST_PLATFORM_AUTH_V1__ = bridge;
	return bridge;
}

const unauth = () => new Response('{"detail":"Not authenticated"}', { status: 401 });
const page = () =>
	new Response(JSON.stringify({ documents: [], table_id: "tbl", total: 0 }), {
		status: 200,
		headers: { "content-type": "application/json" },
	});

describe("session renewal on 401 (same-origin v1 transport)", () => {
	it("renews once and retries the request", async () => {
		const bridge = installBridge();
		const fetchMock = vi.fn().mockResolvedValueOnce(unauth()).mockResolvedValueOnce(page());
		vi.stubGlobal("fetch", fetchMock);

		await expect(tables.query("t1")).resolves.toMatchObject({ table_id: "tbl" });
		expect(bridge.refreshAccessToken).toHaveBeenCalledTimes(1);
		expect(fetchMock).toHaveBeenCalledTimes(2);
		expect(bridge.handleAuthenticationFailure).not.toHaveBeenCalled();
	});

	it("sends the rotated CSRF token on the retried mutation", async () => {
		document.cookie = "csrf_token=old";
		installBridge({
			refreshAccessToken: vi.fn(async () => {
				document.cookie = "csrf_token=new";
				return true;
			}),
		});
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(unauth())
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ id: "r1", data: {} }), {
					status: 201,
					headers: { "content-type": "application/json" },
				}),
			);
		vi.stubGlobal("fetch", fetchMock);

		await tables.insert("t1", { k: "v" });
		const csrf = (call: unknown[]) =>
			new Headers((call[1] as RequestInit).headers).get("X-CSRF-Token");
		expect(csrf(fetchMock.mock.calls[0])).toBe("old");
		expect(csrf(fetchMock.mock.calls[1])).toBe("new");
		expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBe(
			(fetchMock.mock.calls[0][1] as RequestInit).body,
		);
	});

	it("applies normal status handling to the retried response (404 → null)", async () => {
		installBridge();
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValueOnce(unauth()).mockResolvedValueOnce(new Response(null, { status: 404 })),
		);
		await expect(tables.get("t1", "row-1")).resolves.toBeNull();
	});

	it("hands off to the host and throws the 401 when renewal fails", async () => {
		const bridge = installBridge({ refreshAccessToken: vi.fn(async () => false) });
		const fetchMock = vi.fn().mockResolvedValue(unauth());
		vi.stubGlobal("fetch", fetchMock);

		await expect(tables.query("t1")).rejects.toThrow(/tables: 401/);
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(bridge.handleAuthenticationFailure).toHaveBeenCalledTimes(1);
	});

	it("retries at most once and hands off when the retry is still 401", async () => {
		const bridge = installBridge();
		const fetchMock = vi.fn().mockImplementation(async () => unauth());
		vi.stubGlobal("fetch", fetchMock);

		await expect(tables.query("t1")).rejects.toThrow(/tables: 401/);
		expect(fetchMock).toHaveBeenCalledTimes(2);
		expect(bridge.refreshAccessToken).toHaveBeenCalledTimes(1);
		expect(bridge.handleAuthenticationFailure).toHaveBeenCalledTimes(1);
	});

	it("does not renew when the host forbids it (embed session)", async () => {
		const bridge = installBridge({ canRefreshAccessToken: () => false });
		const fetchMock = vi.fn().mockResolvedValue(unauth());
		vi.stubGlobal("fetch", fetchMock);

		await expect(tables.query("t1")).rejects.toThrow(/tables: 401/);
		expect(bridge.refreshAccessToken).not.toHaveBeenCalled();
		expect(bridge.handleAuthenticationFailure).not.toHaveBeenCalled();
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("keeps today's behavior when no host bridge exists", async () => {
		const fetchMock = vi.fn().mockResolvedValue(unauth());
		vi.stubGlobal("fetch", fetchMock);
		await expect(tables.query("t1")).rejects.toThrow(/tables: 401/);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it("leaves renewal to the provider transport's fetchImpl (v2)", async () => {
		const bridge = installBridge();
		const fetchImpl = vi.fn().mockResolvedValue(unauth());
		restoreTransport = setBifrostTransport({ baseUrl: "https://api.example", fetchImpl });

		await expect(tables.query("t1")).rejects.toThrow(/tables: 401/);
		expect(fetchImpl).toHaveBeenCalledTimes(1);
		expect(bridge.refreshAccessToken).not.toHaveBeenCalled();
	});

	it("recovers a parallel burst with one shared renewal", async () => {
		// Mirror the host's single-flight lock.
		let inflight: Promise<boolean> | null = null;
		let renewed = false;
		const refresh = vi.fn(() => {
			inflight ??= Promise.resolve().then(() => {
				renewed = true;
				return true;
			});
			return inflight;
		});
		installBridge({ refreshAccessToken: refresh });
		vi.stubGlobal(
			"fetch",
			vi.fn().mockImplementation(async () => (renewed ? page() : unauth())),
		);

		const results = await Promise.all(Array.from({ length: 8 }, (_, i) => tables.query(`t${i}`)));
		expect(results).toHaveLength(8);
		expect(results.every((r) => r.table_id === "tbl")).toBe(true);
		// Every caller asks; the host lock collapses them into one renewal.
		expect(await inflight).toBe(true);
	});
});
```

- [ ] **Step 2: Run. Expect FAIL** on the renewal tests (today a 401 throws immediately and `fetch` is called once)

Run: `cd client && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/tables.test.ts`

- [ ] **Step 3: Implement in `tables.ts`.** Add `getPlatformAuth` to the existing `./transport` import. Then restructure `http()` so the request is built by a function (CSRF is read per attempt), and add the renewal step before the existing status handling:

```ts
import { getBifrostTransport, getPlatformAuth } from "./transport";
```

```ts
async function http<T>(
  path: string,
  init: RequestInit = {},
  options: { throwOnNotFound?: boolean } = {},
): Promise<T | null> {
  const method = (init.method ?? "GET").toUpperCase();
  const transport = getBifrostTransport();
  const usingProvider = Boolean(transport.baseUrl || transport.headers);
  const url = transport.baseUrl
    ? `${transport.baseUrl.replace(/\/$/, "")}${path}`
    : path;
  const doFetch = transport.fetchImpl ?? fetch;
  // Built per attempt: a session refresh rotates the csrf_token cookie, so a
  // retry must carry the new value, not the one read before the refresh.
  const send = () => {
    // Same-origin (v1) uses cookie + CSRF. A provider transport (v2) carries its
    // own auth headers (bearer) and targets a possibly cross-origin baseUrl, so
    // CSRF/cookies don't apply.
    const csrfHeaders: Record<string, string> =
      usingProvider || method === "GET" || method === "HEAD"
        ? {}
        : { "X-CSRF-Token": getCsrfToken() };
    return doFetch(url, {
      ...init,
      credentials: usingProvider ? "omit" : "include",
      headers: {
        "content-type": "application/json",
        ...csrfHeaders,
        ...(transport.headers ?? {}),
        ...(init.headers ?? {}),
      },
    });
  };
  let r = await send();
  // v1 apps run on the host's 30-minute session cookie, and nothing else in
  // the app renews it. Renew through the host's single-flight refresh and
  // retry once. A provider transport (v2) already does this in its fetchImpl.
  if (r.status === 401 && !usingProvider) {
    const auth = getPlatformAuth();
    if (auth?.canRefreshAccessToken()) {
      if (await auth.refreshAccessToken()) r = await send();
      if (r.status === 401) auth.handleAuthenticationFailure();
    }
  }
  if (r.status === 403) {
    // ...existing 403 / 404 / 204 / !ok / json handling, unchanged...
```

Keep everything from `if (r.status === 403)` to the end of the function exactly as it is today.

- [ ] **Step 4: Run. Expect PASS** (all of `tables.test.ts`, old and new)

Run: `cd client && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/tables.test.ts`

- [ ] **Step 5: Commit**

```bash
git add client/src/lib/app-sdk/tables.ts client/src/lib/app-sdk/tables.test.ts
git commit -m "fix(app-sdk): renew session and retry once when v1 table calls 401"
```

---

### Task 3: Prove `useTable` recovers end-to-end

**Files:**
- Test: `client/src/lib/app-sdk/use-table.test.tsx`

**Interfaces:**
- Consumes: Task 2 behavior through the public `useTable` hook. No production code changes.

- [ ] **Step 1: Write the test.** Append inside the existing top-level `describe`. Add `import type { PlatformAuthBridge } from "./transport";` and an `afterEach` that deletes the bridge global.

```tsx
it("recovers the snapshot after the session cookie lapses (v1 renewal)", async () => {
	const refreshAccessToken = vi.fn(async () => true);
	(globalThis as typeof globalThis & { __BIFROST_PLATFORM_AUTH_V1__?: PlatformAuthBridge })
		.__BIFROST_PLATFORM_AUTH_V1__ = {
		getAccessToken: () => null,
		canRefreshAccessToken: () => true,
		refreshAccessToken,
		handleAuthenticationFailure: vi.fn(),
	};
	vi.stubGlobal(
		"fetch",
		vi
			.fn()
			.mockResolvedValueOnce(new Response('{"detail":"Not authenticated"}', { status: 401 }))
			.mockResolvedValueOnce(makePage(["p1", "p2"], 2)),
	);

	const { result } = renderHook(() => useTable("crm-providers-local"));
	await waitFor(() => expect(result.current.loading).toBe(false));
	expect(result.current.error).toBeNull();
	expect(result.current.rows.map((r) => r.id)).toEqual(["p1", "p2"]);
	expect(refreshAccessToken).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run. Expect PASS**

Run: `cd client && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/use-table.test.tsx`

- [ ] **Step 3: Prove the test has teeth.** Temporarily restore the pre-fix `tables.ts` from `main`. Don't use `git stash`, because the stash stack is shared across worktrees. Expect FAIL (`rows: []`, `error` set), then restore the fix:

```bash
git show origin/main:client/src/lib/app-sdk/tables.ts > client/src/lib/app-sdk/tables.ts
(cd client && NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/use-table.test.tsx)   # expect FAIL
git checkout HEAD -- client/src/lib/app-sdk/tables.ts
```

- [ ] **Step 4: Commit**

```bash
git add client/src/lib/app-sdk/use-table.test.tsx
git commit -m "test(app-sdk): useTable recovers after v1 session renewal"
```

---

### Task 4: Verify and prepare the PR

- [ ] **Step 1: Focused suites + type/lint gates**

```bash
cd client
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/app-sdk/
npm run tsc
npm run lint
```
Expected: all green. Report exact commands and results in the PR.

- [ ] **Step 2: "After" browser run on the same worktree stack.** HMR has already served the fixed `tables.ts`. Rerun the Task 0 Step 6 harness unchanged except `-e PHASE=after`. Expected:
  - `card1` = `Vonage Business`, `card1Error` empty
  - `save` = `saved ok`
  - `statuses` shows `401 …/documents/query` → `200 POST /api/auth/refresh` → `200 …/documents/query`, and the insert ends `201`
  - Screenshot at `$SCR/out/after.png`

- [ ] **Step 3: Unrecoverable path.** Add a second test to the harness that clears **both** `access_token` and `refresh_token`, then clicks Save. Expected after the fix: the page lands on `/login?returnTo=…` (host `handleAuthenticationFailure`). Before the fix it would have stayed put showing `tables: 401`.

- [ ] **Step 4: Report before vs after to the user.** Show both screenshots and the two `statuses` logs side by side, plus the unit/tsc/lint results. Then stop for approval.

- [ ] **Step 5: Push and open the PR (only after the user approves)**

Title: `fix(app-sdk): renew session on 401 for v1 table calls`

The description covers: the symptom (v1 apps lose reads and saves 30 minutes after the last host refresh; error `tables: 401 {"detail":"Not authenticated"}`), the root cause (cookie-only `http()` has no renewal; v2 is unaffected via `authedFetch`), the fix (renew via host bridge + retry once, same-origin only, CSRF re-read), tests run, and out-of-scope follow-ups:
- `files.ts` has the same cookie-only pattern
- the v1 websocket treats 4001 as non-retryable, so live updates stop after expiry until remount; `useTable` snapshot refresh on reconnect depends on it
- app-side: Braytel CRM ignores `useTable().error`

End the description with:

🤖 Generated with [Claude Code](https://claude.com/claude-code)
