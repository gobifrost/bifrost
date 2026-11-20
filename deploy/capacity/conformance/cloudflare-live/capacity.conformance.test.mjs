import assert from "node:assert/strict";
import test from "node:test";

import conformanceProvider, { WorkerContainer } from "./capacity.conformance.mjs";

const WORKER = "11111111-2222-4333-8444-555555555555";
const OTHER_WORKER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const UID = "suite-resource-1";
const ENV = {
    SCOPE: "suite-scope",
    WORKER_VERSION: "test-build-1",
    MEMORY_LIMIT_BYTES: String(2 * 1024 ** 3),
    MAX_WORKERS: "2",
    MAX_LIFETIME_SECONDS: "3600",
    STARTING_GRACE_MS: "0",
    CAPACITY_TOKEN: "c".repeat(32),
    CONFORMANCE_CONTROL_TOKEN: "s".repeat(32),
    WORKER_CONTAINER_ENV: "{}",
};

const route = (workerId = WORKER) => `https://capacity.example.test/__conformance/v1/workers/${workerId}/stop`;
const headers = (overrides = {}) => ({
    Authorization: `Bearer ${ENV.CONFORMANCE_CONTROL_TOKEN}`,
    "X-Bifrost-Capacity-Scope": ENV.SCOPE,
    "Content-Type": "application/json",
    ...overrides,
});
const request = (workerId = WORKER, body = { resource_uid: UID }, overrides = {}) => new Request(route(workerId), {
    method: "POST",
    headers: headers(overrides),
    body: JSON.stringify(body),
});

function fakeContainer({ running = true, lifecycle } = {}) {
    const calls = { destroy: 0, monitor: 0 };
    const storage = new Map();
    if (lifecycle !== undefined) storage.set("lifecycle", lifecycle);
    const container = {
        running,
        async setInactivityTimeout() {},
        async destroy() {
            calls.destroy += 1;
            this.running = false;
        },
        monitor() {
            calls.monitor += 1;
            return new Promise(() => {});
        },
    };
    const ctx = {
        container,
        waitUntil: () => {},
        blockConcurrencyWhile: (operation) => operation(),
        storage: {
            get: async (key) => storage.get(key),
            put: async (key, value) => storage.set(key, structuredClone(value)),
        },
    };
    return { ctx, calls, storage };
}

function runningLifecycle(workerId = WORKER, resourceUid = UID) {
    return {
        version: 1,
        binding: { worker_id: workerId, resource_uid: resourceUid },
        start_requested: true,
        destroyed: false,
        exit: null,
    };
}

async function stopThroughContainer(worker, workerId = WORKER, resourceUid = UID, overrides = {}) {
    return worker.fetch(new Request("https://do/__conformance/stop", {
        method: "POST",
        headers: headers({ "X-Bifrost-Conformance-Worker": workerId, ...overrides }),
        body: JSON.stringify({ resource_uid: resourceUid }),
    }));
}

test("the conformance route rejects missing or malformed control configuration", async () => {
    let forwards = 0;
    const target = { getByName: () => ({ fetch: async () => { forwards += 1; return new Response(); } }) };

    const unauthenticated = await conformanceProvider.fetch(
        request(WORKER, { resource_uid: UID }, { Authorization: "" }), { ...ENV, WORKER_CONTAINER: target },
    );
    const malformed = await conformanceProvider.fetch(
        request(), { ...ENV, CONFORMANCE_CONTROL_TOKEN: "short", WORKER_CONTAINER: target },
    );
    const missing = await conformanceProvider.fetch(
        request(), { ...ENV, CONFORMANCE_CONTROL_TOKEN: undefined, WORKER_CONTAINER: target },
    );
    const invalidBase = await conformanceProvider.fetch(
        request(), { ...ENV, SCOPE: "", WORKER_CONTAINER: target },
    );

    assert.equal(unauthenticated.status, 401);
    assert.equal(malformed.status, 503);
    assert.equal(missing.status, 503);
    assert.equal(invalidBase.status, 503);
    assert.equal(forwards, 0);
});

test("the conformance route rejects a wrong scope without forwarding", async () => {
    let forwards = 0;
    const response = await conformanceProvider.fetch(request(WORKER, { resource_uid: UID }, {
        "X-Bifrost-Capacity-Scope": "other-scope",
    }), {
        ...ENV,
        WORKER_CONTAINER: { getByName: () => ({ fetch: async () => { forwards += 1; return new Response(); } }) },
    });

    assert.equal(response.status, 401);
    assert.equal(forwards, 0);
});

test("the container refuses a wrong UID or an unstarted identity without destroying", async () => {
    const exact = fakeContainer({ lifecycle: runningLifecycle() });
    const worker = new WorkerContainer(exact.ctx, ENV);
    const wrongUid = await stopThroughContainer(worker, WORKER, "other-resource");
    const numericUid = await stopThroughContainer(worker, WORKER, 123);
    const wrongMethod = await worker.fetch(new Request("https://do/__conformance/stop", {
        headers: headers({ "X-Bifrost-Conformance-Worker": WORKER }),
    }));

    const unstarted = fakeContainer({ lifecycle: {
        ...runningLifecycle(), start_requested: false,
    } });
    const unstartedWorker = new WorkerContainer(unstarted.ctx, ENV);
    const unstartedResponse = await stopThroughContainer(unstartedWorker);

    assert.equal(wrongUid.status, 409);
    assert.equal(numericUid.status, 400);
    assert.equal(wrongMethod.status, 405);
    assert.equal(unstartedResponse.status, 409);
    assert.equal(exact.calls.destroy, 0);
    assert.equal(unstarted.calls.destroy, 0);
});

test("the container route destroys only the exact running incarnation without tombstoning it", async () => {
    const fake = fakeContainer({ lifecycle: runningLifecycle() });
    const worker = new WorkerContainer(fake.ctx, ENV);

    const response = await stopThroughContainer(worker);

    assert.equal(response.status, 202);
    assert.equal(fake.calls.destroy, 1);
    assert.equal(fake.storage.get("lifecycle").destroyed, false);
    assert.equal(fake.storage.get("lifecycle").exit, null);
});

test("the public test route forwards the exact binding to the matching container DO", async () => {
    let named = null;
    let forwarded = null;
    const response = await conformanceProvider.fetch(request(), {
        ...ENV,
        WORKER_CONTAINER: {
            getByName(workerId) {
                named = workerId;
                return { fetch: async (_url, init) => {
                    forwarded = init;
                    return new Response(JSON.stringify({ ok: true }), { status: 202 });
                } };
            },
        },
    });

    assert.equal(response.status, 202);
    assert.equal(named, WORKER);
    assert.equal(forwarded.headers["X-Bifrost-Conformance-Worker"], WORKER);
    assert.deepEqual(JSON.parse(forwarded.body), { resource_uid: UID });
});

test("ordinary provider requests remain delegated to the production entrypoint", async () => {
    const response = await conformanceProvider.fetch(
        new Request("https://capacity.example.test/v1/capabilities", {
            headers: { Authorization: `Bearer ${ENV.CAPACITY_TOKEN}` },
        }),
        { ...ENV, CONFORMANCE_CONTROL_TOKEN: "bad" },
    );

    assert.equal(response.status, 200);
    assert.equal((await response.json()).scope, ENV.SCOPE);
});

test("the test route cannot target a different UUID through a matching lifecycle", async () => {
    const fake = fakeContainer({ lifecycle: runningLifecycle() });
    const worker = new WorkerContainer(fake.ctx, ENV);

    const response = await stopThroughContainer(worker, OTHER_WORKER);

    assert.equal(response.status, 409);
    assert.equal(fake.calls.destroy, 0);
});
