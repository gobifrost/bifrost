import assert from "node:assert/strict";
import test from "node:test";
import { WORKER_ENTRYPOINT, buildWorkerEnv, createCapacityProvider } from "./capacity.mjs";

const TOKEN = "synthetic-capacity-token-" + "x".repeat(32);
const SCOPE = "test-scope";
const NETWORK = JSON.stringify([
    { hostname: "db.internal", address: "127.0.0.2", port: 5432, url: "wss://gateway.example.invalid/network/db" },
    { hostname: "cache.internal", address: "127.0.0.3", port: 6379, url: "wss://gateway.example.invalid/network/cache" },
    { hostname: "s3.internal", address: "127.0.0.4", port: 8333, url: "wss://gateway.example.invalid/network/s3" },
]);

function setup(overrides = {}) {
    const launched = [];
    const stopped = [];
    const provider = createCapacityProvider({
        settings: {
            token: TOKEN,
            scope: SCOPE,
            workerVersion: "test-build-1",
            memoryLimitBytes: 2 * 1024 * 1024 * 1024,
            maxWorkers: 2,
            maxLifetimeSeconds: 21600,
            image: "ghcr.io/example/bifrost-worker-dev@sha256:deadbeef",
            apiOrigin: "https://api.example.invalid",
            secretKey: "s".repeat(48),
            environment: "production",
            databaseUrl: "postgresql+asyncpg://worker:secret@db.internal:5432/bifrost",
            databaseUrlSync: "postgresql://worker:secret@db.internal:5432/bifrost",
            redisUrl: "redis://cache.internal:6379/0",
            s3EndpointUrl: "http://s3.internal:8333",
            s3AccessKey: "synthetic-access",
            s3SecretKey: "synthetic-secret",
            s3Bucket: "bifrost-test",
            s3Region: "us-east-1",
            workerNetwork: NETWORK,
            networkToken: "n".repeat(48),
            ...overrides,
        },
        store: new Map(),
        runtime: {
            async launchWorker(spec) {
                launched.push(spec);
            },
            async stopWorker(name) {
                stopped.push(name);
            },
        },
    });
    return { provider, launched, stopped };
}

const RESERVATION = {
    worker_id: "11111111-2222-4333-8444-555555555555",
    worker_version: "test-build-1",
    memory_limit_bytes: 2 * 1024 * 1024 * 1024,
    max_lifetime_seconds: 21600,
    drain_grace_seconds: 300,
};

function request(path, method, token = TOKEN, { scope = SCOPE, body = undefined } = {}) {
    return new Request(`https://capacity.example.invalid${path}`, {
        method,
        headers: {
            ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
            ...(scope === null ? {} : { "X-Bifrost-Capacity-Scope": scope }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
}

async function prepare(provider, reservation = RESERVATION) {
    const response = await provider.fetch(request(`/v1/workers/${reservation.worker_id}`, "PUT", TOKEN, { body: reservation }));
    assert.equal(response.status, 200);
    return response.json();
}

test("missing or wrong bearer token cannot reach the provider", async () => {
    const { provider } = setup();
    for (const token of ["", "Bearer wrong", `Bearer ${"y".repeat(TOKEN.length)}`, null]) {
        const response = await provider.fetch(request("/v1/capabilities", "GET", token));
        assert.equal(response.status, 401);
        assert.equal(response.headers.get("Cache-Control"), "no-store");
    }
});

test("scope header must match exactly", async () => {
    const { provider } = setup();
    for (const scope of ["other-scope", "", null]) {
        const response = await provider.fetch(request("/v1/capabilities", "GET", TOKEN, { scope }));
        assert.equal(response.status, 400);
    }
});

test("capabilities report protocol version 1 and the configured bounds", async () => {
    const { provider } = setup();
    const response = await provider.fetch(request("/v1/capabilities", "GET"));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
        protocol_version: 1,
        scope: SCOPE,
        worker_version: "test-build-1",
        memory_limit_bytes: 2147483648,
        max_workers: 2,
        max_lifetime_seconds: 21600,
    });
});

test("unknown workers read 404", async () => {
    const { provider } = setup();
    const response = await provider.fetch(
        request("/v1/workers/99999999-2222-4333-8444-555555555555", "GET"),
    );
    assert.equal(response.status, 404);
});

test("prepare is idempotent for the same reservation", async () => {
    const { provider } = setup();
    const first = await prepare(provider);
    const second = await prepare(provider);
    assert.deepEqual(second, first);
    assert.equal(first.state, "prepared");
    assert.match(first.resource_uid, /^[a-zA-Z0-9._:-]{1,255}$/);
});

test("prepare rejects mismatched worker id, build, and ranges", async () => {
    const { provider } = setup();
    const wrongPath = await provider.fetch(
        request("/v1/workers/99999999-2222-4333-8444-555555555555", "PUT", TOKEN, { body: RESERVATION }),
    );
    assert.equal(wrongPath.status, 400);
    const wrongBuild = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}`, "PUT", TOKEN, {
            body: { ...RESERVATION, worker_version: "other-build" },
        }),
    );
    assert.equal(wrongBuild.status, 409);
    const badRange = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}`, "PUT", TOKEN, {
            body: { ...RESERVATION, memory_limit_bytes: 1024 },
        }),
    );
    assert.equal(badRange.status, 400);
});

test("conflicting reservation for a live worker is rejected, never relabeled", async () => {
    const { provider } = setup();
    await prepare(provider);
    const response = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}`, "PUT", TOKEN, {
            body: { ...RESERVATION, max_lifetime_seconds: 3600 },
        }),
    );
    assert.equal(response.status, 409);
});

test("start launches once with enrollment identity, then is idempotent", async () => {
    const { provider, launched } = setup();
    const prepared = await prepare(provider);
    const first = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}/start`, "POST", TOKEN, {
            body: { resource_uid: prepared.resource_uid },
        }),
    );
    assert.equal(first.status, 200);
    assert.equal((await first.json()).state, "starting");
    assert.equal(launched.length, 1);
    assert.equal(launched[0].name, `bifrost-${RESERVATION.worker_id}`);
    assert.deepEqual(launched[0].entrypoint, WORKER_ENTRYPOINT);
    const env = launched[0].env;
    assert.equal(env.BIFROST_WORKER_FORM, "external");
    assert.equal(env.BIFROST_WORKER_INCARNATION_ID, RESERVATION.worker_id);
    assert.equal(env.BIFROST_WORKER_RESOURCE_UID, prepared.resource_uid);
    assert.equal(env.BIFROST_API_URL, "https://api.example.invalid");
    assert.equal(env.BIFROST_SECRET_KEY, "s".repeat(48));
    assert.equal(env.BIFROST_POOL_WORKER_EXECUTION_LANE, "normal");
    assert.equal(env.BIFROST_POOL_MAX_LIFETIME_SECONDS, "21600");
    assert.equal(env.BIFROST_DATABASE_URL, "postgresql+asyncpg://worker:secret@db.internal:5432/bifrost");
    assert.equal(env.BIFROST_REDIS_URL, "redis://cache.internal:6379/0");
    assert.equal(env.BIFROST_S3_BUCKET, "bifrost-test");
    assert.equal(env.HOME, "/home/bifrost");
    assert.deepEqual(JSON.parse(env.BIFROST_WORKER_NETWORK).map(({ hostname }) => hostname),
        ["db.internal", "cache.internal", "s3.internal"]);
    const second = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}/start`, "POST", TOKEN, {
            body: { resource_uid: prepared.resource_uid },
        }),
    );
    assert.equal(second.status, 200);
    assert.equal(launched.length, 1);
});

test("start rejects unknown workers and UID mismatches", async () => {
    const { provider, launched } = setup();
    const missing = await provider.fetch(
        request("/v1/workers/99999999-2222-4333-8444-555555555555/start", "POST", TOKEN, {
            body: { resource_uid: "res-x" },
        }),
    );
    assert.equal(missing.status, 404);
    await prepare(provider);
    const mismatch = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}/start`, "POST", TOKEN, {
            body: { resource_uid: "res-other" },
        }),
    );
    assert.equal(mismatch.status, 409);
    assert.equal(launched.length, 0);
});

test("delete writes an idempotent tombstone and never restarts", async () => {
    const { provider, launched, stopped } = setup();
    const prepared = await prepare(provider);
    await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}/start`, "POST", TOKEN, {
            body: { resource_uid: prepared.resource_uid },
        }),
    );
    const first = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}`, "DELETE", TOKEN, {
            body: { resource_uid: prepared.resource_uid },
        }),
    );
    assert.equal(first.status, 200);
    assert.equal((await first.json()).state, "deleted");
    assert.deepEqual(stopped, [`bifrost-${RESERVATION.worker_id}`]);
    const second = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}`, "DELETE", TOKEN, {
            body: { resource_uid: prepared.resource_uid },
        }),
    );
    assert.equal(second.status, 200);
    assert.equal(stopped.length, 1);
    const restart = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}/start`, "POST", TOKEN, {
            body: { resource_uid: prepared.resource_uid },
        }),
    );
    assert.equal(restart.status, 409);
    assert.equal(launched.length, 1);
    const reprepare = await provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}`, "PUT", TOKEN, { body: RESERVATION }),
    );
    assert.equal(reprepare.status, 409);
});

test("worker env carries the full Normal-workload contract", () => {
    const env = buildWorkerEnv({
        apiOrigin: "https://api.example.invalid",
        secretKey: "s".repeat(48),
        environment: "production",
        databaseUrl: "postgresql+asyncpg://worker:secret@db.internal:5432/bifrost",
        databaseUrlSync: "postgresql://worker:secret@db.internal:5432/bifrost",
        redisUrl: "redis://cache.internal:6379/0",
        s3EndpointUrl: "http://s3.internal:8333",
        s3AccessKey: "access",
        s3SecretKey: "secret",
        s3Bucket: "bucket",
        workerNetwork: NETWORK,
        networkToken: "n".repeat(48),
    }, { worker_id: RESERVATION.worker_id, max_lifetime_seconds: 900 }, "res-123");
    assert.equal(env.BIFROST_WORKER_FORM, "external");
    assert.equal(env.BIFROST_POOL_MAX_LIFETIME_SECONDS, "900");
    assert.equal(env.BIFROST_DATABASE_POOL_SIZE, "1");
    assert.equal(env.PIP_NO_CACHE_DIR, "1");
    assert.ok(!("BIFROST_S3_REGION" in env));
    assert.ok(!("BIFROST_WORKER_CONTROL_HEADERS" in env));
});

test("worker env rejects a control secret too short to verify hellos", () => {
    assert.throws(() => buildWorkerEnv({
        apiOrigin: "https://api.example.invalid",
        secretKey: "short",
        environment: "production",
        databaseUrl: "postgresql+asyncpg://worker:secret@db.internal:5432/bifrost",
        databaseUrlSync: "postgresql://worker:secret@db.internal:5432/bifrost",
        redisUrl: "redis://cache.internal:6379/0",
        s3EndpointUrl: "http://s3.internal:8333",
        s3AccessKey: "access",
        s3SecretKey: "secret",
        s3Bucket: "bucket",
        workerNetwork: NETWORK,
        networkToken: "n".repeat(48),
    }, { worker_id: RESERVATION.worker_id, max_lifetime_seconds: 900 }, "res-123"), /SECRET_KEY/);
});

test("worker env rejects dependency hostnames outside the private transport", () => {
    const base = {
        apiOrigin: "https://api.example.invalid",
        secretKey: "s".repeat(48),
        environment: "production",
        databaseUrlSync: "postgresql://worker:secret@db.internal:5432/bifrost",
        redisUrl: "redis://cache.internal:6379/0",
        s3EndpointUrl: "http://s3.internal:8333",
        s3AccessKey: "access",
        s3SecretKey: "secret",
        s3Bucket: "bucket",
        workerNetwork: NETWORK,
        networkToken: "n".repeat(48),
    };
    assert.throws(() => buildWorkerEnv({
        ...base, databaseUrl: "postgresql+asyncpg://worker:secret@unmapped.example:5432/bifrost",
    }, { worker_id: RESERVATION.worker_id, max_lifetime_seconds: 900 }, "res-123"), /NETWORK target/);
    assert.throws(() => buildWorkerEnv({
        ...base,
        databaseUrl: "postgresql+asyncpg://worker:secret@db.internal:5432/bifrost",
        workerNetwork: "not-json",
    }, { worker_id: RESERVATION.worker_id, max_lifetime_seconds: 900 }, "res-123"), /JSON/);
});

test("worker env allows public HTTPS object storage without a tunnel target", () => {
    const env = buildWorkerEnv({
        apiOrigin: "https://api.example.invalid",
        secretKey: "s".repeat(48),
        environment: "production",
        databaseUrl: "postgresql+asyncpg://worker:secret@db.internal:5432/bifrost",
        databaseUrlSync: "postgresql://worker:secret@db.internal:5432/bifrost",
        redisUrl: "redis://cache.internal:6379/0",
        s3EndpointUrl: "https://s3.us-east-1.amazonaws.com",
        s3AccessKey: "access",
        s3SecretKey: "secret",
        s3Bucket: "bucket",
        s3Region: "us-east-1",
        workerNetwork: NETWORK,
        networkToken: "n".repeat(48),
        controlHeaders: JSON.stringify({ "CF-Access-Client-Id": "id" }),
    }, { worker_id: RESERVATION.worker_id, max_lifetime_seconds: 900 }, "res-123");
    assert.equal(env.BIFROST_S3_ENDPOINT_URL, "https://s3.us-east-1.amazonaws.com");
    assert.equal(env.BIFROST_S3_REGION, "us-east-1");
    assert.equal(env.BIFROST_WORKER_CONTROL_HEADERS, JSON.stringify({ "CF-Access-Client-Id": "id" }));
    assert.throws(() => buildWorkerEnv({
        apiOrigin: "https://api.example.invalid",
        secretKey: "s".repeat(48),
        environment: "production",
        databaseUrl: "postgresql+asyncpg://worker:secret@db.internal:5432/bifrost",
        databaseUrlSync: "postgresql://worker:secret@db.internal:5432/bifrost",
        redisUrl: "redis://cache.internal:6379/0",
        s3EndpointUrl: "http://s3.us-east-1.amazonaws.com",
        s3AccessKey: "access",
        s3SecretKey: "secret",
        s3Bucket: "bucket",
        workerNetwork: NETWORK,
        networkToken: "n".repeat(48),
    }, { worker_id: RESERVATION.worker_id, max_lifetime_seconds: 900 }, "res-123"), /Private S3/);
});

test("start fails closed when the worker env is misconfigured", async () => {
    const { provider, launched } = setup({ secretKey: "short" });
    const prepared = await prepare(provider);
    await assert.rejects(provider.fetch(
        request(`/v1/workers/${RESERVATION.worker_id}/start`, "POST", TOKEN, {
            body: { resource_uid: prepared.resource_uid },
        }),
    ), /SECRET_KEY/);
    assert.equal(launched.length, 0);
    const status = await provider.fetch(request(`/v1/workers/${RESERVATION.worker_id}`, "GET"));
    assert.equal((await status.json()).state, "prepared");
});
