// Contract tests for the capability engine. No Cloudflare APIs: the engine is
// injected with an in-memory store and a fake runtime, so these run on plain
// `node --test`.

import assert from "node:assert/strict";
import test from "node:test";

import { createEngine, ProtocolError, STATE } from "./capacity-core.mjs";

const PROFILE = {
    scope: "test-scope",
    worker_version: "test-build-1",
    memory_limit_bytes: 2 * 1024 ** 3,
    max_workers: 2,
    max_lifetime_seconds: 3600,
    starting_grace_ms: 0,
};

const WORKER = "11111111-2222-4333-8444-555555555555";

function setup({ maxWorkers = PROFILE.max_workers } = {}) {
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
    const runtime = {
        async start(row) {
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

test("capabilities profile is stable", () => {
    const { engine } = setup();
    assert.equal(typeof engine.get, "function");
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
