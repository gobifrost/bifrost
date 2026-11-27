// Contract tests for the capability engine. No Cloudflare APIs: the engine is
// injected with an in-memory store and a fake runtime, so these run on plain
// `node --test`.

import assert from "node:assert/strict";
import test from "node:test";

import { createEngine, ProtocolError, STATE } from "./capacity-core.mjs";
import cloudflareProvider, {
    containerStartEnv,
    workerControlKey,
    inactivityTimeoutMs,
    readSettings,
    ScopeRegistry,
    WorkerContainer,
    WorkerLedger,
} from "./capacity.mjs";

const PROFILE = {
    scope: "test-scope",
    worker_version: "test-build-1",
    memory_limit_bytes: 2 * 1024 ** 3,
    max_workers: 2,
    max_lifetime_seconds: 3600,
    starting_grace_ms: 0,
};

const WORKER = "11111111-2222-4333-8444-555555555555";

function setup({
    maxWorkers = PROFILE.max_workers,
    failBeforeContainerHandler = false,
    unprovenStartOutcome = false,
} = {}) {
    const rows = new Map();
    const launches = [];
    const store = {
        read: async (id) => rows.get(id) ?? null,
        write: async (id, row) => {
            rows.set(id, row);
            return row;
        },
        countLive: async (scope) =>
            [...rows.values()].filter(
                (row) => row.scope === scope && ["prepared", "starting", "running"].includes(row.state),
            ).length,
    };
    let running = false;
    let failNextStart = false;
    let failBeforeHandler = failBeforeContainerHandler;
    const runtime = {
        async start(row) {
            if (failBeforeHandler) {
                failBeforeHandler = false;
                throw new Error("container DO was unavailable before its handler ran");
            }
            if (failNextStart) {
                failNextStart = false;
                // The launch was accepted but the response was lost.
                running = true;
                launches.push(row.resource_uid);
                throw new Error("lost response");
            }
            launches.push(row.resource_uid);
            running = true;
            return { state: STATE.STARTING, handle: { container: row.worker_id } };
        },
        async destroy() {
            running = false;
            return { state: STATE.DELETED };
        },
        async observe(row) {
            if (running) return { state: STATE.RUNNING, handle: { container: row.worker_id } };
            if (unprovenStartOutcome && row.intent === "start") {
                return { state: STATE.PREPARED, handle: row.handle ?? {} };
            }
            if (failBeforeContainerHandler && row.intent === "start") {
                return {
                    state: STATE.PREPARED,
                    start_fact: "never_started",
                    handle: row.handle ?? {},
                };
            }
            const attempted = row.intent === "start" || row.started_at != null;
            return { state: attempted ? STATE.STOPPED : STATE.PREPARED, handle: row.handle ?? {} };
        },
    };
    const engine = createEngine({
        profile: { ...PROFILE, max_workers: maxWorkers },
        store,
        runtime,
    });
    return {
        engine,
        rows,
        launches,
        runtime,
        loseNextStartResponse: () => {
            failNextStart = true;
        },
        stopExternally: () => {
            running = false;
        },
    };
}

const reservation = (overrides = {}) => ({
    worker_id: WORKER,
    worker_version: PROFILE.worker_version,
    memory_limit_bytes: PROFILE.memory_limit_bytes,
    max_lifetime_seconds: 1800,
    drain_grace_seconds: 300,
    ...overrides,
});

async function prepared(ctx) {
    const doc = await ctx.engine.prepare(WORKER, reservation());
    return doc.resource_uid;
}

test("capabilities profile is hardware and build only", async () => {
    const { engine } = setup();
    assert.equal(typeof engine.get, "function");
    const response = await cloudflareProvider.fetch(
        new Request("https://provider/v1/capabilities", {
            headers: { Authorization: `Bearer ${ENV.CAPACITY_TOKEN}` },
        }),
        ENV,
    );
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
        protocol_version: 1,
        scope: "test-scope",
        worker_version: "test-build-1",
        memory_limit_bytes: 2 * 1024 ** 3,
        max_workers: 2,
        max_lifetime_seconds: 3600,
    });
});

test("unknown worker reads null", async () => {
    const { engine } = setup();
    assert.equal(await engine.get(WORKER), null);
});

test("prepare is idempotent and launches nothing", async () => {
    const ctx = setup();
    const first = await ctx.engine.prepare(WORKER, reservation());
    assert.equal(first.state, STATE.PREPARED);
    const again = await ctx.engine.prepare(WORKER, reservation());
    assert.equal(again.resource_uid, first.resource_uid);
    assert.deepEqual(ctx.launches, []);
});

test("conflicting reservation is rejected", async () => {
    const ctx = setup();
    const uid = await prepared(ctx);
    await assert.rejects(
        () => ctx.engine.prepare(WORKER, reservation({ memory_limit_bytes: 1 })),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
    const after = await ctx.engine.get(WORKER);
    assert.equal(after.resource_uid, uid);
});

test("start requires the bound resource UID", async () => {
    const ctx = setup();
    await prepared(ctx);
    await assert.rejects(
        () => ctx.engine.start(WORKER, { resource_uid: "wrong" }),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
    assert.deepEqual(ctx.launches, []);
});

test("start launches exactly one container", async () => {
    const ctx = setup();
    const uid = await prepared(ctx);
    const doc = await ctx.engine.start(WORKER, { resource_uid: uid });
    assert.ok([STATE.STARTING, STATE.RUNNING].includes(doc.state));
    assert.equal(ctx.launches.length, 1);
});

test("replay does not launch a second container", async () => {
    const ctx = setup();
    const uid = await prepared(ctx);
    await ctx.engine.start(WORKER, { resource_uid: uid });
    await ctx.engine.start(WORKER, { resource_uid: uid });
    await ctx.engine.start(WORKER, { resource_uid: uid });
    assert.equal(ctx.launches.length, 1);
});

test("a lost start response does not double-launch on retry", async () => {
    const ctx = setup();
    const uid = await prepared(ctx);
    ctx.loseNextStartResponse();
    await assert.rejects(() => ctx.engine.start(WORKER, { resource_uid: uid }));
    // Retry must observe the accepted container instead of starting another.
    const doc = await ctx.engine.start(WORKER, { resource_uid: uid });
    assert.ok([STATE.STARTING, STATE.RUNNING].includes(doc.state));
    assert.equal(ctx.launches.length, 1);
});

test("a start request that never reaches the container handler is safely replayed", async () => {
    const ctx = setup({ failBeforeContainerHandler: true });
    const uid = await prepared(ctx);
    await assert.rejects(() => ctx.engine.start(WORKER, { resource_uid: uid }));

    const replay = await ctx.engine.start(WORKER, { resource_uid: uid });

    assert.ok([STATE.STARTING, STATE.RUNNING].includes(replay.state));
    assert.equal(ctx.launches.length, 1);
});

test("an unproven start outcome fails closed instead of replaying a possible launch", async () => {
    const ctx = setup({ unprovenStartOutcome: true, failBeforeContainerHandler: true });
    const uid = await prepared(ctx);
    await assert.rejects(() => ctx.engine.start(WORKER, { resource_uid: uid }));

    await assert.rejects(
        () => ctx.engine.start(WORKER, { resource_uid: uid }),
        (error) => error instanceof ProtocolError && error.status === 502,
    );
    assert.equal(ctx.launches.length, 0);
});

test("a concurrent delete fences a started incarnation from resurrection", async () => {
    const ctx = setup();
    const uid = await prepared(ctx);
    let releaseStart;
    const blocked = new Promise((resolve) => {
        releaseStart = resolve;
    });
    const start = ctx.runtime.start;
    ctx.runtime.start = async (row) => {
        await blocked;
        return start(row);
    };

    const starting = ctx.engine.start(WORKER, { resource_uid: uid });
    await new Promise((resolve) => setImmediate(resolve));
    const deleting = ctx.engine.remove(WORKER, { resource_uid: uid });
    releaseStart();
    await starting;
    const deleted = await deleting;

    assert.equal(deleted.state, STATE.DELETED);
    await assert.rejects(
        () => ctx.engine.start(WORKER, { resource_uid: uid }),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
    assert.equal(ctx.launches.length, 1);
});

test("a stopped incarnation is terminal and never restarts", async () => {
    const ctx = setup();
    const uid = await prepared(ctx);
    await ctx.engine.start(WORKER, { resource_uid: uid });
    ctx.stopExternally();
    const observed = await ctx.engine.get(WORKER);
    assert.equal(observed.state, STATE.STOPPED);
    await assert.rejects(
        () => ctx.engine.start(WORKER, { resource_uid: uid }),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
    assert.equal(ctx.launches.length, 1);
});

test("delete tombstones and cannot be resurrected", async () => {
    const ctx = setup();
    const uid = await prepared(ctx);
    await ctx.engine.start(WORKER, { resource_uid: uid });
    const deleted = await ctx.engine.remove(WORKER, { resource_uid: uid });
    assert.equal(deleted.state, STATE.DELETED);
    assert.equal((await ctx.engine.get(WORKER)).state, STATE.DELETED);
    await assert.rejects(
        () => ctx.engine.prepare(WORKER, reservation()),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
    await assert.rejects(
        () => ctx.engine.start(WORKER, { resource_uid: uid }),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
});

test("delete requires the bound resource UID", async () => {
    const ctx = setup();
    await prepared(ctx);
    await assert.rejects(
        () => ctx.engine.remove(WORKER, { resource_uid: "wrong" }),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
    assert.equal((await ctx.engine.get(WORKER)).state, STATE.PREPARED);
});

test("capacity ceiling rejects a new reservation", async () => {
    const ctx = setup({ maxWorkers: 1 });
    await prepared(ctx);
    const second = "22222222-2222-4333-8444-555555555555";
    await assert.rejects(
        () => ctx.engine.prepare(second, reservation({ worker_id: second })),
        (error) => error instanceof ProtocolError && error.status === 409,
    );
});

test("foreign scope is invisible", async () => {
    const ctx = setup();
    await prepared(ctx);
    const row = ctx.rows.get(WORKER);
    row.scope = "other-scope";
    assert.equal(await ctx.engine.get(WORKER), null);
});

// The container-facing seams below are not exercised by the engine tests: they
// only matter once a real container is launched. Both defects they guard
// against were found by live acceptance, not by the protocol suite.

test("launched container env carries the launcher identity and a hostname", () => {
    const env = containerStartEnv(
        { BIFROST_API_URL: "https://api.example.invalid" },
        { worker_id: WORKER, resource_uid: "uid-1" },
        WORKER,
        "a".repeat(64),
    );
    assert.equal(env.BIFROST_API_URL, "https://api.example.invalid");
    assert.equal(env.BIFROST_WORKER_CONTROL_SECRET, "a".repeat(64));
    assert.equal(env.BIFROST_WORKER_FORM, "external");
    assert.equal(env.BIFROST_WORKER_INCARNATION_ID, WORKER);
    assert.equal(env.BIFROST_WORKER_RESOURCE_UID, "uid-1");
    assert.equal(env.BIFROST_POOL_WORKER_EXECUTION_LANE, "normal");
    // An empty HOSTNAME makes the launcher identity refuse to start.
    assert.equal(env.HOSTNAME, `cf-${WORKER}`);
});

test("worker control key agrees with the Python API and binds the resource", async () => {
    const key = await workerControlKey(
        "synthetic-provider-credential-for-unit-test", WORKER, "resource-1",
    );
    assert.equal(key, "740ab8ee7b20fcc7e0d19de991e5d6d11ab564677b9703b68cab58ac20ba9771");
    assert.notEqual(key, await workerControlKey(
        "synthetic-provider-credential-for-unit-test", WORKER, "resource-2",
    ));
});

test("API-only container configuration refuses platform credentials", () => {
    const ledger = new WorkerLedger({}, {
        ...ENV, WORKER_CONTAINER_ENV: JSON.stringify({ BIFROST_SECRET_KEY: "forbidden" }),
    });
    assert.throws(() => ledger.runtime(WORKER), /no platform credentials/);
});

test("inactivity timeout covers the worker lifetime within the platform cap", () => {
    // A container is stopped when its Durable Object goes idle unless this
    // timeout holds it open, so it must exceed the worker's own lifetime.
    assert.equal(inactivityTimeoutMs(60), 660_000);
    assert.equal(inactivityTimeoutMs(7200), 7_800_000);
    assert.equal(inactivityTimeoutMs(6 * 3600), 21_600_000);
    assert.equal(inactivityTimeoutMs(604800), 21_600_000);
});

// --- container Durable Object, auth, and template -------------------------

const ENV = {
    SCOPE: "test-scope",
    WORKER_VERSION: "test-build-1",
    MEMORY_LIMIT_BYTES: String(2 * 1024 ** 3),
    MAX_WORKERS: "2",
    MAX_LIFETIME_SECONDS: "3600",
    STARTING_GRACE_MS: "0",
    CAPACITY_TOKEN: "t".repeat(32),
    WORKER_CONTAINER_ENV: "{}",
};

function fakeContainer({ running = false, destroyFails = false, monitor = null } = {}) {
    const calls = { start: 0, holds: [], destroy: 0, monitors: 0 };
    const storage = new Map();
    const container = {
        running,
        start(options) {
            calls.start += 1;
            calls.options = options;
            this.running = true;
        },
        async setInactivityTimeout(ms) {
            calls.holds.push(ms);
        },
        async destroy() {
            calls.destroy += 1;
            if (destroyFails) throw new Error("destroy failed");
            this.running = false;
        },
        monitor() {
            calls.monitors += 1;
            return monitor ?? new Promise(() => {});
        },
    };
    const ctx = {
        container,
        waitUntil: () => {},
        blockConcurrencyWhile: (fn) => fn(),
        storage: {
            get: async (key) => storage.get(key),
            put: async (key, value) => storage.set(key, value),
        },
    };
    return { ctx, calls, storage };
}

const startRequest = () =>
    new Request("https://do/start", {
        method: "POST",
        body: JSON.stringify({
            env: { A: "1" },
            entrypoint: ["python", "-m", "src.worker.main"],
            binding: { worker_id: WORKER, resource_uid: "uid-1" },
        }),
    });

const lifecycleRequest = (path, resourceUid = "uid-1") =>
    new Request(`https://do/${path}`, {
        method: "POST",
        body: JSON.stringify({ binding: { worker_id: WORKER, resource_uid: resourceUid } }),
    });

test("an uncertain durable intent write never crosses the physical start seam", async () => {
    const { ctx, calls, storage } = fakeContainer();
    ctx.storage.put = async (key, value) => {
        storage.set(key, structuredClone(value));
        throw new Error("write acknowledgement lost");
    };
    const worker = new WorkerContainer(ctx, ENV);
    assert.equal((await worker.fetch(startRequest())).status, 500);
    assert.equal(calls.start, 0);
    ctx.storage.put = async (key, value) => storage.set(key, structuredClone(value));
    const restarted = new WorkerContainer(ctx, ENV);
    await restarted.fetch(startRequest());
    assert.equal(calls.start, 0);
    const status = await restarted.fetch(lifecycleRequest("status"));
    assert.notEqual((await status.json()).lifecycle, "never_started");
});

test("accepted start is durable before a process can exit without a reply", async () => {
    const { ctx, calls, storage } = fakeContainer();
    ctx.container.start = function () {
        assert.equal(storage.get("lifecycle").start_requested, true);
        calls.start += 1;
        this.running = false;
        throw new Error("runtime reply lost after process exit");
    };
    const worker = new WorkerContainer(ctx, ENV);
    assert.equal((await worker.fetch(startRequest())).status, 500);
    const restarted = new WorkerContainer(ctx, ENV);
    await restarted.fetch(startRequest());
    assert.equal(calls.start, 1);
    assert.notEqual((await (await restarted.fetch(lifecycleRequest("status"))).json()).lifecycle, "never_started");
});

test("accepted cold startup is held open before running is observed", async () => {
    const { ctx, calls } = fakeContainer();
    ctx.container.start = function () {
        calls.start += 1;
        // Runtime admission can precede the running observation.
        this.running = false;
    };
    const worker = new WorkerContainer(ctx, ENV);
    const response = await worker.fetch(startRequest());
    assert.equal(response.status, 200);
    assert.equal((await response.json()).lifecycle, "accepted");
    assert.deepEqual(calls.holds, [inactivityTimeoutMs(3600)]);
    await worker.fetch(startRequest());
    assert.equal(calls.start, 1);
});

test("start launches once and holds the container open", async () => {
    const { ctx, calls } = fakeContainer();
    const worker = new WorkerContainer(ctx, ENV);
    const response = await worker.fetch(startRequest());
    assert.equal(response.status, 200);
    assert.equal(calls.start, 1);
    // Without this the container is stopped as soon as the DO goes idle.
    assert.deepEqual(calls.holds, [inactivityTimeoutMs(3600)]);
    assert.equal(calls.monitors, 1);
});

test("a repeated start holds the container without launching a second one", async () => {
    const { ctx, calls } = fakeContainer();
    const worker = new WorkerContainer(ctx, ENV);
    await worker.fetch(startRequest());
    const response = await worker.fetch(startRequest());
    assert.equal(response.status, 200);
    assert.equal(calls.start, 1);
    // The retry must re-apply the hold: a failed first attempt may have started
    // the container and died before setting it.
    assert.equal(calls.holds.length, 2);
});

test("a lost reply after an accepted start survives a container DO restart", async () => {
    const { ctx, calls, storage } = fakeContainer();
    const first = new WorkerContainer(ctx, ENV);
    await first.fetch(startRequest());
    assert.equal(storage.get("lifecycle").start_requested, true);

    const restarted = new WorkerContainer(ctx, ENV);
    await new Promise((resolve) => setImmediate(resolve));
    const status = await restarted.fetch(lifecycleRequest("status"));
    const replay = await restarted.fetch(startRequest());

    assert.deepEqual(await status.json(), { running: true, lifecycle: "accepted" });
    assert.equal(replay.status, 200);
    assert.equal(calls.start, 1);
});

test("a durable container exit fact prevents an incarnation from starting again", async () => {
    let rejectMonitor;
    const monitor = new Promise((_resolve, reject) => {
        rejectMonitor = reject;
    });
    const { ctx, calls, storage } = fakeContainer({ monitor });
    const worker = new WorkerContainer(ctx, ENV);
    await worker.fetch(startRequest());
    ctx.container.running = false;
    rejectMonitor({ exitCode: 17, message: "worker exited" });
    await new Promise((resolve) => setImmediate(resolve));

    const status = await worker.fetch(lifecycleRequest("status"));
    const replay = await worker.fetch(startRequest());

    assert.deepEqual(await status.json(), { running: false, lifecycle: "exited" });
    assert.equal(storage.get("lifecycle").exit.code, 17);
    assert.equal(replay.status, 409);
    assert.equal(calls.start, 1);
});

test("container lifecycle facts reject a reused worker name with another resource UID", async () => {
    const { ctx } = fakeContainer();
    const worker = new WorkerContainer(ctx, ENV);
    await worker.fetch(startRequest());

    const status = await worker.fetch(lifecycleRequest("status", "uid-2"));

    assert.equal(status.status, 409);
});

test("destroy on a stopped container succeeds without signalling", async () => {
    const { ctx, calls } = fakeContainer({ running: false });
    const worker = new WorkerContainer(ctx, ENV);
    const response = await worker.fetch(
        new Request("https://do/destroy", {
            method: "POST",
            body: JSON.stringify({ binding: { worker_id: WORKER, resource_uid: "uid-1" } }),
        }),
    );
    assert.equal(response.status, 200);
    assert.equal(calls.destroy, 0);
});

test("a failing destroy is reported instead of looking like a deletion", async () => {
    const { ctx } = fakeContainer({ destroyFails: true });
    const worker = new WorkerContainer(ctx, ENV);
    await worker.fetch(startRequest());
    const response = await worker.fetch(
        new Request("https://do/destroy", {
            method: "POST",
            body: JSON.stringify({ binding: { worker_id: WORKER, resource_uid: "uid-1" } }),
        }),
    );
    // The ledger must not record `deleted` for a container that still runs.
    assert.equal(response.status, 500);
});

test("settings refuse a deployment with no capacity token", () => {
    // Without this the bearer comparison accepts the literal "Bearer undefined".
    assert.throws(() => readSettings({ ...ENV, CAPACITY_TOKEN: undefined }));
    assert.throws(() => readSettings({ ...ENV, CAPACITY_TOKEN: "short" }));
    assert.doesNotThrow(() => readSettings(ENV));
});

test("the shipped template declares an instance type that can unpack the image", async () => {
    const { readFile } = await import("node:fs/promises");
    const toml = await readFile(new URL("./wrangler.toml", import.meta.url), "utf8");
    // Omitting instance_type creates a 256 MiB / 2 GB app that fails to pull.
    assert.match(toml, /instance_type\s*=\s*"standard-1"/);
    assert.match(toml, /max_instances\s*=\s*[1-9]/);
    assert.match(toml, /MAX_LIFETIME_SECONDS\s*=\s*"21600"/);
});

test("the ledger launches the container with the identity it needs", async () => {
    let captured = null;
    const env = {
        ...ENV,
        WORKER_ENTRYPOINT_JSON: '["python","-m","src.worker.main"]',
        WORKER_CONTAINER: {
            getByName: () => ({
                fetch: async (_url, init) => {
                    captured = JSON.parse(init.body);
                    return new Response("{}", { status: 200 });
                },
            }),
        },
    };
    const ledger = new WorkerLedger({}, env);
    await ledger.runtime(WORKER).start({ worker_id: WORKER, resource_uid: "uid-1" });
    // Guards the wiring: a launch without these never enrolls.
    assert.equal(captured.env.BIFROST_WORKER_FORM, "external");
    assert.equal(captured.env.BIFROST_WORKER_INCARNATION_ID, WORKER);
    assert.equal(captured.env.BIFROST_WORKER_RESOURCE_UID, "uid-1");
    assert.equal(captured.env.HOSTNAME, `cf-${WORKER}`);
    assert.deepEqual(captured.entrypoint, ["python", "-m", "src.worker.main"]);
});

test("the ledger refuses to record a container destroy that failed", async () => {
    const env = {
        ...ENV,
        WORKER_CONTAINER: {
            getByName: () => ({ fetch: async () => new Response("{}", { status: 500 }) }),
        },
    };
    const ledger = new WorkerLedger({}, env);
    await assert.rejects(
        () => ledger.runtime(WORKER).destroy({ worker_id: WORKER, resource_uid: "uid-1" }),
        /destroy failed/,
    );
});

test("a restarted container DO re-applies the hold without a full env", async () => {
    const { ctx, calls, storage } = fakeContainer({ running: true });
    storage.set("lifecycle", {
        version: 1, binding: { worker_id: WORKER, resource_uid: "uid-1" },
        start_requested: true, destroyed: false, exit: null,
    });
    // Recovery must depend only on the timeout setting: an unrelated
    // misconfiguration must not stop it from holding a running container.
    new WorkerContainer(ctx, { MAX_LIFETIME_SECONDS: "3600" });
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(calls.holds, [inactivityTimeoutMs(3600)]);
});

// --- scope registry: reservation generations -----------------------------

function fakeRegistry() {
    const store = new Map();
    const ctx = {
        storage: {
            get: async (key) => store.get(key),
            put: async (key, value) => {
                store.set(key, value);
            },
        },
    };
    return new ScopeRegistry(ctx, {});
}

test("a stale reservation cleanup cannot free a re-reserved slot", async () => {
    const registry = fakeRegistry();
    // A rejected prepare takes a reservation...
    const rejected = await registry.reserve("w1", 1);
    // ...a concurrent valid prepare for the same id claims the slot...
    const accepted = await registry.reserve("w1", 1);
    assert.equal(rejected.created, true);
    assert.equal(accepted.created, false);
    assert.notEqual(rejected.generation, accepted.generation);

    // ...so the rejected request's cleanup is a no-op instead of freeing a slot
    // the accepted worker is using.
    await registry.release("w1", rejected.generation);
    assert.deepEqual(await registry.live(), ["w1"]);

    await registry.release("w1", accepted.generation);
    assert.deepEqual(await registry.live(), []);
});

test("the ceiling admits only workers that hold a slot", async () => {
    const registry = fakeRegistry();
    assert.equal((await registry.reserve("w1", 1)).ok, true);
    assert.equal((await registry.reserve("w2", 1)).ok, false);
    await registry.release("w1");
    assert.equal((await registry.reserve("w2", 1)).ok, true);
});

// --- worker ledger: capacity accounting around the record ------------------

function fakeLedger({ live = [], ceiling = 2, failReleaseOnce = false } = {}) {
    const store = new Map();
    const slots = [...live];
    const releases = [];
    const reservations = [];
    const escapes = [];
    let releaseFailuresLeft = failReleaseOnce ? 1 : 0;
    const ctx = {
        storage: {
            get: async (key) => store.get(key),
            put: async (key, value) => {
                store.set(key, value);
            },
        },
        // Mirrors the real primitive: a rejection here resets the object, so the
        // tests can assert that ordinary rejections never reach it.
        blockConcurrencyWhile: async (fn) => {
            try {
                return await fn();
            } catch (error) {
                escapes.push(error);
                throw error;
            }
        },
    };
    const env = {
        ...ENV,
        ...(ceiling === 2 ? {} : { MAX_WORKERS: String(ceiling) }),
        SCOPE_REGISTRY: {
            getByName: () => ({
                fetch: async (target, init) => {
                    const path = String(target);
                    const body = init?.body ? JSON.parse(init.body) : {};
                    if (path.endsWith("/live")) {
                        return new Response(JSON.stringify({ live: slots }), { status: 200 });
                    }
                    if (path.endsWith("/reserve")) {
                        reservations.push(body);
                        if (slots.includes(body.worker_id)) {
                            return new Response(JSON.stringify({ ok: true, generation: 99 }), {
                                status: 200,
                            });
                        }
                        if (slots.length >= (body.ceiling ?? ceiling)) {
                            return new Response(JSON.stringify({ ok: false }), { status: 409 });
                        }
                        slots.push(body.worker_id);
                        return new Response(JSON.stringify({ ok: true, generation: 1 }), {
                            status: 200,
                        });
                    }
                    if (path.endsWith("/release")) {
                        if (releaseFailuresLeft > 0) {
                            releaseFailuresLeft -= 1;
                            throw new Error("registry unavailable");
                        }
                        releases.push(body);
                        const index = slots.indexOf(body.worker_id);
                        if (index >= 0) slots.splice(index, 1);
                    }
                    return new Response("{}", { status: 200 });
                },
            }),
        },
        WORKER_CONTAINER: {
            getByName: () => ({
                fetch: async () =>
                    new Response(JSON.stringify({ running: false }), { status: 200 }),
            }),
        },
    };
    return { ctx, env, slots, releases, reservations, escapes };
}

function ledgerCall(ctx, env, method, body) {
    const ledger = new WorkerLedger(ctx, env);
    return ledger.fetch(
        new Request(`https://do/v1/workers/${WORKER}`, {
            method,
            body: body === undefined ? undefined : JSON.stringify(body),
        }),
    );
}

test("a prepared worker holds exactly one slot", async () => {
    const { ctx, env, slots, releases } = fakeLedger();
    const response = await ledgerCall(ctx, env, "PUT", reservation());
    assert.equal(response.status, 200);
    assert.deepEqual(slots, [WORKER]);
    assert.deepEqual(releases, []);
});

test("a rejected prepare releases the slot it reserved", async () => {
    const { ctx, env, slots, releases, escapes } = fakeLedger();
    const response = await ledgerCall(
        ctx,
        env,
        "PUT",
        reservation({ worker_version: "other-build" }),
    );
    assert.equal(response.status, 409);
    // Nothing may keep capacity for a reservation that was never written.
    assert.deepEqual(slots, []);
    assert.equal(releases.length, 1);
    // An escaping rejection would reset the Durable Object.
    assert.deepEqual(escapes, []);
});

test("a failed re-prepare keeps an admitted worker's slot", async () => {
    const { ctx, env, slots } = fakeLedger();
    const admitted = await ledgerCall(ctx, env, "PUT", reservation());
    assert.equal(admitted.status, 200);
    assert.deepEqual(slots, [WORKER]);

    // A second prepare for the same worker with a different build is rejected,
    // and must not evict the slot the admitted record still owns.
    const rejected = await ledgerCall(
        ctx,
        env,
        "PUT",
        reservation({ worker_version: "other-build" }),
    );
    assert.equal(rejected.status, 409);
    assert.deepEqual(slots, [WORKER]);
});

test("a prepare replayed after deletion leaves no slot behind", async () => {
    const { ctx, env, slots } = fakeLedger();
    await ledgerCall(ctx, env, "PUT", reservation());
    const doc = await (await ledgerCall(ctx, env, "GET")).json();
    const deleted = await ledgerCall(ctx, env, "DELETE", { resource_uid: doc.resource_uid });
    assert.equal(deleted.status, 200);
    assert.deepEqual(slots, []);

    // A replay reserves, is rejected as terminal, and must clean up after
    // itself instead of permanently shrinking MAX_WORKERS.
    const replay = await ledgerCall(ctx, env, "PUT", reservation());
    assert.equal(replay.status, 409);
    assert.deepEqual(slots, []);
});

test("a worker refused by the ceiling does not disturb the admitted one", async () => {
    const { ctx, env, slots } = fakeLedger({ live: ["other-worker"], ceiling: 1 });
    const response = await ledgerCall(ctx, env, "PUT", reservation());
    assert.equal(response.status, 409);
    assert.deepEqual(slots, ["other-worker"]);
});

test("a malformed prepare body does not take a slot", async () => {
    const { ctx, env, slots, reservations } = fakeLedger();
    const ledger = new WorkerLedger(ctx, env);
    const response = await ledger.fetch(
        new Request(`https://do/v1/workers/${WORKER}`, { method: "PUT", body: "{not json" }),
    );
    assert.equal(response.status, 500);
    assert.deepEqual(reservations, []);
    assert.deepEqual(slots, []);
});

test("a delete whose release failed is finished by the retry", async () => {
    const { ctx, env, slots } = fakeLedger({ failReleaseOnce: true });
    await ledgerCall(ctx, env, "PUT", reservation());
    const doc = await (await ledgerCall(ctx, env, "GET")).json();

    const first = await ledgerCall(ctx, env, "DELETE", { resource_uid: doc.resource_uid });
    // The deletion is durable but cleanup failed: capacity is held
    // conservatively, never over-admitted.
    assert.equal(first.status, 500);
    assert.deepEqual(slots, [WORKER]);

    // A retried delete completes the cleanup rather than reporting a terminal
    // record, so the slot cannot be stranded permanently.
    const retry = await ledgerCall(ctx, env, "DELETE", { resource_uid: doc.resource_uid });
    assert.equal(retry.status, 200);
    assert.deepEqual(slots, []);
});


test("container launch failure exposes a bounded stage without runtime secrets", async (t) => {
    const { ctx } = fakeContainer();
    const messages = [];
    t.mock.method(console, "error", (message) => messages.push(message));
    ctx.container.start = () => { throw new TypeError("secret-worker-credential"); };
    const worker = new WorkerContainer(ctx, ENV);
    const response = await worker.fetch(startRequest());
    assert.equal(response.status, 500);
    const body = await response.text();
    assert.equal(JSON.parse(body).error, "container:container_start:TypeError");
    assert.deepEqual(messages, ["container:container_start:TypeError"]);
    assert.ok(!body.includes("secret-worker-credential"));
});


test("allocation failure is witnessed even when timeout setup rejects", async () => {
    let rejectMonitor;
    const monitor = new Promise((_resolve, reject) => { rejectMonitor = reject; });
    // Keep the negative control from producing a separate unhandled rejection.
    monitor.catch(() => {});
    const { ctx, calls } = fakeContainer({ monitor });
    const observations = [];
    ctx.waitUntil = (promise) => observations.push(promise);
    ctx.container.start = function () { calls.start += 1; this.running = false; };
    ctx.container.setInactivityTimeout = async () => {
        const error = new Error("There is no container instance that can be provided to this Durable Object, try again later");
        rejectMonitor(error);
        throw error;
    };
    const worker = new WorkerContainer(ctx, ENV);
    assert.equal((await worker.fetch(startRequest())).status, 500);
    assert.equal(calls.monitors, 1);
    await Promise.all(observations);
    assert.equal((await (await worker.fetch(lifecycleRequest("status"))).json()).lifecycle, "exited");
    assert.equal((await worker.fetch(startRequest())).status, 409);
    assert.equal(calls.start, 1, "the failed incarnation must never be restarted");
});

test("deleting an accepted pending start cancels the physical request", async () => {
    const { ctx, calls } = fakeContainer();
    ctx.container.start = function () { calls.start += 1; this.running = false; };
    const worker = new WorkerContainer(ctx, ENV);
    await worker.fetch(startRequest());
    assert.equal((await worker.fetch(lifecycleRequest("destroy"))).status, 200);
    assert.equal(calls.destroy, 1, "not-running is not proof that startup was cancelled");
    assert.equal((await worker.fetch(startRequest())).status, 409);
});

test("a terminal incarnation cannot hold a late container alive after restart", async () => {
    const { ctx, calls } = fakeContainer();
    const first = new WorkerContainer(ctx, ENV);
    await first.fetch(startRequest());
    await first.fetch(lifecycleRequest("destroy"));
    const holds = calls.holds.length;
    ctx.container.running = true;
    const restarted = new WorkerContainer(ctx, ENV);
    assert.equal((await restarted.fetch(startRequest())).status, 409);
    assert.equal(ctx.container.running, false);
    assert.equal(calls.holds.length, holds);
});


test("a running container without a bound fact is not held or adopted", async () => {
    const { ctx, calls } = fakeContainer({ running: true });
    const worker = new WorkerContainer(ctx, ENV);
    assert.equal((await worker.fetch(lifecycleRequest("status"))).status, 502);
    assert.deepEqual(calls.holds, []);
    assert.equal(calls.start, 0);
    assert.equal(calls.destroy, 0);
});


test("the default worker command retains the production image privilege drop", async () => {
    let entrypoint;
    const ledger = new WorkerLedger({}, {
        ...ENV,
        WORKER_CONTAINER: {
            getByName: () => ({ fetch: async (_url, init) => {
                entrypoint = JSON.parse(init.body).entrypoint;
                return new Response("{}", { status: 200 });
            } }),
        },
    });
    await ledger.runtime(WORKER).start({ worker_id: WORKER, resource_uid: "uid-1" });
    assert.deepEqual(entrypoint, ["/entrypoint.sh", "python", "-m", "src.worker.main"]);
});
