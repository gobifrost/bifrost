// Cloudflare Containers capacity provider.
//
// Transport + durable state only. The protocol lifecycle lives in
// capacity-core.mjs, which is pure and tested without a deployment.
//
// Three Durable Object classes:
//   WorkerLedger    one per incarnation — owns that worker's record and engine
//   ScopeRegistry   one per scope — atomic capacity accounting
//   WorkerContainer container-enabled — the only class that talks to the
//                   Containers binding, so the runtime seam is a single file
//
// The ledger is the source of truth for idempotency, tombstones, and the
// no-restart fence. Container state is only ever read back through it.

import { createEngine, ProtocolError, STATE } from "./capacity-core.mjs";

const SCOPE_HEADER = "X-Bifrost-Capacity-Scope";
const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

const json = (status, body) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// The environment a launched incarnation runs with. Pure, so the identity
// contract is testable without deploying a container.
export async function workerControlKey(providerKey, workerId, resourceUid) {
    if (!UUID_RE.test(workerId) || !/^[a-zA-Z0-9._:-]{1,255}$/.test(resourceUid)) {
        throw new Error("invalid external worker resource identity");
    }
    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
        "raw", encoder.encode(providerKey), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
    );
    const binding = JSON.stringify([workerId.toLowerCase(), resourceUid]);
    const signature = await crypto.subtle.sign(
        "HMAC", key, encoder.encode(`bifrost.external-worker.control.v1\0${binding}`),
    );
    return [...new Uint8Array(signature)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function containerStartEnv(base, row, workerId, controlKey) {
    if (!/^[a-f0-9]{64}$/.test(controlKey)) throw new Error("worker control key is required");
    return {
        ...base,
        BIFROST_WORKER_CONTROL_SECRET: controlKey,
        BIFROST_WORKER_FORM: "external",
        BIFROST_WORKER_INCARNATION_ID: row.worker_id,
        BIFROST_WORKER_RESOURCE_UID: row.resource_uid,
        BIFROST_POOL_WORKER_EXECUTION_LANE: "normal",
        // Container runtimes are not required to export a hostname, and
        // Cloudflare does not. The launcher identity refuses to start without
        // one, and Docker and Kubernetes setting it is a convention rather than
        // a guarantee, so the provider supplies it.
        HOSTNAME: `cf-${workerId}`,
    };
}

// Cloudflare stops a container shortly after the Durable Object that started it
// goes idle unless an inactivity timeout holds it open. Cover the worker's
// lifetime, capped at the platform's six-hour maximum.
export function inactivityTimeoutMs(maxLifetimeSeconds) {
    return Math.min(maxLifetimeSeconds + 600, 6 * 3600) * 1000;
}

// The only setting container recovery needs. Kept separate from readSettings so
// an unrelated misconfiguration cannot stop a restarted Durable Object from
// re-applying the hold to a container that is already running.
function lifetimeSeconds(env) {
    const value = Number(env.MAX_LIFETIME_SECONDS);
    if (!Number.isInteger(value) || value < 60 || value > 6 * 3600) {
        throw new Error("invalid or missing MAX_LIFETIME_SECONDS");
    }
    return value;
}

export function readSettings(env) {
    const text = (name, pattern) => {
        const value = env[name];
        if (typeof value !== "string" || !pattern.test(value)) {
            throw new Error(`invalid or missing ${name}`);
        }
        return value;
    };
    const integer = (name, min, max) => {
        const value = Number(env[name]);
        if (!Number.isInteger(value) || value < min || value > max) {
            throw new Error(`invalid or missing ${name}`);
        }
        return value;
    };
    return {
        scope: text("SCOPE", /^[a-zA-Z0-9._:-]{1,128}$/),
        workerVersion: text("WORKER_VERSION", /^.{1,100}$/),
        memoryLimitBytes: integer("MEMORY_LIMIT_BYTES", 64 * 1024 * 1024, 128 * 1024 ** 3),
        maxWorkers: integer("MAX_WORKERS", 1, 1000),
        // Cloudflare caps a container's inactivity timeout at six hours, so a
        // declared lifetime beyond that cannot be held open.
        maxLifetimeSeconds: lifetimeSeconds(env),
        startingGraceMs: integer("STARTING_GRACE_MS", 0, 600_000),
        // Required. Without it the bearer comparison would accept the literal
        // string "Bearer undefined".
        token: text("CAPACITY_TOKEN", /^[^\s]{32,512}$/),
    };
}

const profileFor = (settings) => ({
    protocol_version: 1,
    scope: settings.scope,
    worker_version: settings.workerVersion,
    memory_limit_bytes: settings.memoryLimitBytes,
    max_workers: settings.maxWorkers,
    max_lifetime_seconds: settings.maxLifetimeSeconds,
});

// Physical workers use the API and an incarnation key, never platform-service
// credentials. Refuse legacy privileged recipes rather than dropping fields.
function parseContainerEnv(env) {
    const raw = env.WORKER_CONTAINER_ENV;
    if (typeof raw !== "string" || raw.trim() === "") return {};
    try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("WORKER_CONTAINER_ENV must be a JSON object");
        }
        for (const name of Object.keys(parsed)) {
            if (/^BIFROST_(SECRET_KEY|DATABASE_URL(?:_SYNC)?|REDIS_URL|S3_.*|AZURE_BLOB_.*|WORKER_CONTROL_SECRET)$/.test(name)) {
                throw new Error("privileged platform environment is forbidden");
            }
            if (typeof parsed[name] !== "string") throw new Error("environment values must be strings");
        }
        return parsed;
    } catch {
        throw new Error("WORKER_CONTAINER_ENV requires string values and no platform credentials");
    }
}

// The launcher command. Defaults to the Bifrost worker entrypoint; a JSON array
// override exists so a deployment can point at a differently packaged image
// without editing code.
function parseEntrypoint(env) {
    const raw = env.WORKER_ENTRYPOINT_JSON;
    if (typeof raw !== "string" || raw.trim() === "") {
        return ["python", "-m", "src.worker.main"];
    }
    try {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed) || parsed.length === 0 || !parsed.every((v) => typeof v === "string")) {
            throw new Error("bad shape");
        }
        return parsed;
    } catch {
        throw new Error("WORKER_ENTRYPOINT_JSON must be a non-empty JSON array of strings");
    }
}

// ---------------------------------------------------------------------------
// Scope registry: atomic capacity accounting across incarnations
// ---------------------------------------------------------------------------

export class ScopeRegistry {
    constructor(ctx, env) {
        this.ctx = ctx;
        this.env = env;
    }

    async slots() {
        return (await this.ctx.storage.get("slots")) ?? [];
    }

    async live() {
        return (await this.slots()).map((slot) => slot.id);
    }

    // Each reservation carries a generation. Re-reserving an id claims the slot
    // with a new generation, which invalidates an older request's cleanup: a
    // rejected prepare can only release the reservation it actually took, never
    // the one a concurrent valid prepare is now using.
    async reserve(workerId, ceiling) {
        const slots = await this.slots();
        const generation = ((await this.ctx.storage.get("generation")) ?? 0) + 1;
        await this.ctx.storage.put("generation", generation);

        const existing = slots.find((slot) => slot.id === workerId);
        if (existing) {
            // Membership first: a repeated prepare for a worker that already
            // holds a slot is idempotent and must not be rejected by the ceiling.
            existing.generation = generation;
            await this.ctx.storage.put("slots", slots);
            return { ok: true, generation, created: false };
        }
        if (slots.length >= ceiling) return { ok: false, generation: null, created: false };
        slots.push({ id: workerId, generation });
        await this.ctx.storage.put("slots", slots);
        return { ok: true, generation, created: true };
    }

    // `generation` undefined releases unconditionally (a confirmed deletion).
    async release(workerId, generation) {
        if (generation === undefined) {
            const remaining = (await this.slots()).filter((slot) => slot.id !== workerId);
            await this.ctx.storage.put("slots", remaining);
            return;
        }
        const kept = (await this.slots()).filter(
            (slot) => slot.id !== workerId || slot.generation !== generation,
        );
        await this.ctx.storage.put("slots", kept);
    }

    async fetch(request) {
        const url = new URL(request.url);
        if (url.pathname === "/live") return json(200, { live: await this.live() });
        if (url.pathname === "/reserve") {
            const body = await request.json();
            const reservation = await this.reserve(body.worker_id, body.ceiling);
            return json(reservation.ok ? 200 : 409, reservation);
        }
        if (url.pathname === "/release") {
            const body = await request.json();
            await this.release(body.worker_id, body.generation);
            return json(200, { ok: true });
        }
        return json(404, { error: "not found" });
    }
}

// ---------------------------------------------------------------------------
// Container runtime seam: the ONLY class that touches the Containers binding
// ---------------------------------------------------------------------------

class LifecycleError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

function containerBinding(body) {
    const binding = body?.binding;
    if (
        !binding
        || !UUID_RE.test(binding.worker_id ?? "")
        || !/^[a-zA-Z0-9._:-]{1,255}$/.test(binding.resource_uid ?? "")
    ) {
        throw new LifecycleError(400, "container lifecycle binding is invalid");
    }
    return { worker_id: binding.worker_id, resource_uid: binding.resource_uid };
}

function sameBinding(left, right) {
    return left?.worker_id === right.worker_id && left?.resource_uid === right.resource_uid;
}

export class WorkerContainer {
    constructor(ctx, env) {
        this.ctx = ctx;
        this.env = env;
        this._chain = Promise.resolve();
        // Neither the inactivity timeout nor an exit observer survives a DO
        // restart. The fact stored below is the only safe way to reconnect an
        // existing container to one capacity incarnation.
        this._ready = ctx.blockConcurrencyWhile(() => this.restoreLifecycle());
    }

    get container() {
        const container = this.ctx.container;
        if (!container) {
            // A container-enabled Durable Object always has this. A missing
            // binding is a deployment error, not a template placeholder.
            throw new Error("container binding missing: this DO must be declared as a container class");
        }
        return container;
    }

    // Without an inactivity timeout Cloudflare stops the container shortly
    // after the Durable Object goes idle, which is immediately after the start
    // request returns. A worker incarnation is meant to serve work for its
    // whole lifetime, so hold it open for that long (capped at the platform's
    // six-hour maximum).
    async holdOpen() {
        await this.container.setInactivityTimeout(inactivityTimeoutMs(lifetimeSeconds(this.env)));
    }

    async lifecycle(binding, { allowMissing = true } = {}) {
        const fact = await this.ctx.storage.get("lifecycle");
        if (fact === undefined || fact === null) {
            if (allowMissing) return null;
            throw new LifecycleError(409, "container lifecycle was not initialized");
        }
        if (
            !fact
            || fact.version !== 1
            || !sameBinding(fact.binding, binding)
            || typeof fact.start_requested !== "boolean"
            || typeof fact.destroyed !== "boolean"
        ) {
            throw new LifecycleError(409, "container lifecycle belongs to another incarnation");
        }
        return fact;
    }

    async restoreLifecycle() {
        const fact = await this.ctx.storage.get("lifecycle");
        if (fact?.version !== 1) return;
        if (fact.destroyed || fact.exit) {
            // A delayed attachment cannot revive an immutable terminal UID.
            if (this.ctx.container?.running) await this.container.destroy();
            return;
        }
        if (fact.start_requested) {
            this.observeExit(fact.binding);
            if (this.ctx.container?.running) {
                try {
                    await this.holdOpen();
                } catch {
                    console.error("container:restore_timeout:Error");
                }
            }
        }
    }

    // `monitor()` is the durable exit witness. A `running` read alone cannot
    // tell a failed startup from a request that never reached this DO.
    observeExit(binding) {
        if (this._observing) return;
        this._observing = true;
        this.ctx.waitUntil(
            Promise.resolve().then(() => this.container.monitor()).then(
                () => ({ code: 0, message: null }),
                (error) => ({
                    code: Number.isInteger(error?.exitCode) ? error.exitCode : null,
                    message: String(error?.message ?? error).slice(0, 200),
                }),
            ).then(async (record) => {
                this._observing = false;
                await this.serialize(async () => {
                    const fact = await this.lifecycle(binding);
                    if (fact && !fact.destroyed && !fact.exit) {
                        fact.exit = record;
                        await this.ctx.storage.put("lifecycle", fact);
                    }
                });
                console.log("capacity container exit", JSON.stringify(record));
            }),
        );
    }

    serialize(operation) {
        const next = this._chain.then(operation, operation);
        this._chain = next.catch(() => {});
        return next;
    }

    async fetch(request) {
        return this.serialize(async () => {
            await this._ready;
            return this.handle(request);
        });
    }

    async handle(request) {
        const url = new URL(request.url);
        let stage = "request_validation";
        try {
            if (url.pathname === "/start") {
                const body = await request.json();
                const binding = containerBinding(body);
                const fact = await this.lifecycle(binding);
                if (fact?.destroyed) {
                    throw new LifecycleError(409, "container incarnation was destroyed");
                }
                if (fact?.exit) {
                    throw new LifecycleError(409, "container incarnation already exited");
                }
                if (!fact) {
                    // Persist intent before crossing the physical-start seam.
                    // If this write is uncertain, never invoke start. If the DO
                    // then resets before the call, fail closed for this
                    // incarnation rather than risk launching it twice.
                    if (this.container.running) {
                        throw new LifecycleError(502, "running container has no bound lifecycle fact");
                    }
                    await this.ctx.storage.put("lifecycle", {
                        version: 1,
                        binding,
                        start_requested: true,
                        destroyed: false,
                        exit: null,
                    });
                    stage = "container_start";
                    this.container.start({
                        env: body.env ?? {},
                        entrypoint: body.entrypoint ?? [],
                        enableInternet: true,
                    });
                }
                // Set the timeout after accepting startup, including its cold
                // pending window; a running sample is not a readiness gate.
                // Observe allocation failure before timeout setup can reject.
                // A failed attempt remains terminal for this immutable UID;
                // the controller may provision a new incarnation after cleanup.
                stage = "exit_observer";
                this.observeExit(binding);
                stage = "inactivity_timeout";
                await this.holdOpen();
                return json(200, { running: this.container.running === true, lifecycle: "accepted" });
            }
            if (url.pathname === "/destroy") {
                const binding = containerBinding(await request.json());
                let fact = await this.lifecycle(binding);
                if (fact?.destroyed) return json(200, { running: false, lifecycle: "destroyed" });
                // An already-stopped container is a completed destroy: the
                // caller asked for absence, not for a signal.
                if (!fact && this.container.running) {
                    throw new LifecycleError(502, "running container has no bound lifecycle fact");
                }
                // A pending start can still attach after a not-running sample.
                if (this.container.running || fact?.start_requested) await this.container.destroy();
                fact ??= { version: 1, binding, start_requested: false, destroyed: false, exit: null };
                fact.destroyed = true;
                await this.ctx.storage.put("lifecycle", fact);
                return json(200, { running: false, lifecycle: "destroyed" });
            }
            if (url.pathname === "/status") {
                const binding = containerBinding(await request.json());
                const fact = await this.lifecycle(binding);
                // `running` is liveness, not admission: only the launcher's
                // signed hello proves a worker actually enrolled.
                if (!fact) {
                    if (this.container.running) {
                        throw new LifecycleError(502, "running container has no bound lifecycle fact");
                    }
                    return json(200, { running: false, lifecycle: "never_started" });
                }
                if (fact.destroyed) return json(200, { running: false, lifecycle: "destroyed" });
                if (fact.exit) return json(200, { running: false, lifecycle: "exited" });
                return json(200, {
                    running: this.container.running === true,
                    lifecycle: "accepted",
                });
            }
            return json(404, { error: "not found" });
        } catch (error) {
            const status = error instanceof LifecycleError ? error.status : 500;
            // Runtime messages can contain startup environment values. Retain
            // only fixed stage/type labels so provider logs diagnose a failed
            // launch without leaking worker credentials.
            const kind = error instanceof TypeError ? "TypeError"
                : error instanceof RangeError ? "RangeError"
                : error instanceof LifecycleError ? "LifecycleError" : "Error";
            const diagnostic = `container:${stage}:${kind}`;
            console.error(diagnostic);
            return json(status, { error: diagnostic });
        }
    }
}

// ---------------------------------------------------------------------------
// Per-incarnation ledger: owns the record and the engine
// ---------------------------------------------------------------------------

export class WorkerLedger {
    constructor(ctx, env) {
        this.ctx = ctx;
        this.env = env;
        this.settings = readSettings(env);
    }

    store(workerId) {
        const ctx = this.ctx;
        return {
            read: async (id) => (await ctx.storage.get("row")) ?? null,
            write: async (_id, row) => {
                await ctx.storage.put("row", row);
                return row;
            },
            countLive: async (scope) => {
                const registry = this.env.SCOPE_REGISTRY.getByName(scope);
                const response = await registry.fetch("https://do/live");
                const { live } = await response.json();
                return live.filter((id) => id !== workerId).length;
            },
        };
    }

    runtime(workerId) {
        const containers = this.env.WORKER_CONTAINER;
        const containerEnv = parseContainerEnv(this.env);
        const entrypoint = parseEntrypoint(this.env);
        const providerKey = this.settings.token;
        const stub = () => containers.getByName(workerId);
        return {
            async start(row) {
                const response = await stub().fetch("https://do/start", {
                    method: "POST",
                    body: JSON.stringify({
                        env: containerStartEnv(
                            containerEnv, row, workerId,
                            await workerControlKey(providerKey, workerId, row.resource_uid),
                        ),
                        entrypoint,
                        binding: { worker_id: row.worker_id, resource_uid: row.resource_uid },
                    }),
                });
                if (!response.ok) throw new Error("container start failed");
                return { state: STATE.STARTING, handle: { container: workerId } };
            },
            async destroy(row) {
                const response = await stub().fetch("https://do/destroy", {
                    method: "POST",
                    body: JSON.stringify({ binding: { worker_id: row.worker_id, resource_uid: row.resource_uid } }),
                });
                // A failed destroy must not be recorded as a deletion: the
                // container would keep running, billed and untracked, above
                // MAX_WORKERS.
                if (!response.ok) throw new Error("container destroy failed");
                return { state: STATE.DELETED };
            },
            async observe(row) {
                const response = await stub().fetch("https://do/status", {
                    method: "POST",
                    body: JSON.stringify({ binding: { worker_id: row.worker_id, resource_uid: row.resource_uid } }),
                });
                // A provider error is not evidence that the container stopped.
                if (!response.ok) throw new Error("container status failed");
                const { running, lifecycle } = await response.json();
                if (lifecycle === "never_started") {
                    return { state: STATE.PREPARED, start_fact: "never_started", handle: row.handle ?? {} };
                }
                if (lifecycle === "accepted") {
                    return {
                        state: running ? STATE.RUNNING : STATE.STARTING,
                        start_fact: "accepted",
                        handle: { container: workerId },
                    };
                }
                if (lifecycle === "exited") {
                    return { state: STATE.STOPPED, start_fact: "exited", handle: row.handle ?? {} };
                }
                if (lifecycle === "destroyed") {
                    return { state: STATE.DELETED, start_fact: "destroyed", handle: row.handle ?? {} };
                }
                throw new Error("container lifecycle is invalid");
            },
        };
    }

    engine(workerId) {
        const settings = this.settings;
        // One engine per worker per Durable Object instance. The engine
        // serializes operations on an in-memory chain, so building a fresh one
        // per request would let a concurrent delete and start interleave and
        // resurrect a tombstone.
        this._engines ??= new Map();
        let engine = this._engines.get(workerId);
        if (!engine) {
            engine = createEngine({
                profile: {
                    ...profileFor(settings),
                    starting_grace_ms: settings.startingGraceMs,
                },
                store: this.store(workerId),
                runtime: this.runtime(workerId),
            });
            this._engines.set(workerId, engine);
        }
        return engine;
    }

    async fetch(request) {
        const url = new URL(request.url);
        const workerId = url.pathname.split("/")[3] ?? "";
        const step = url.pathname.split("/")[4] ?? "";
        const engine = this.engine(workerId);
        const registry = () => this.env.SCOPE_REGISTRY.getByName(this.settings.scope);
        // `undefined` releases unconditionally; a confirmed deletion is terminal.
        const release = async (generation) => {
            await registry().fetch("https://do/release", {
                method: "POST",
                body: JSON.stringify({ worker_id: workerId, generation }),
            });
        };

        try {
            // Capacity accounting and the record it protects must not interleave
            // with another operation on this worker. The reservation, the write,
            // and any rollback are one serialized step, so a concurrent prepare
            // can neither claim nor free this worker's slot mid-flight.
            return await this.ctx.blockConcurrencyWhile(async () => {
                // Errors are converted here: an exception escaping this callback
                // resets the Durable Object, and a validation rejection is not a
                // reason to reset it.
                try {
                    if (request.method === "GET") {
                        const doc = await engine.get(workerId);
                        return doc ? json(200, doc) : json(404, { error: "unknown worker" });
                    }
                    if (request.method === "PUT") {
                        // Parse first: a malformed body must not take a slot.
                        const body = await request.json();
                        const reserved = await registry().fetch("https://do/reserve", {
                            method: "POST",
                            body: JSON.stringify({
                                worker_id: workerId,
                                ceiling: this.settings.maxWorkers,
                            }),
                        });
                        const reservation = await reserved.json();
                        if (!reservation.ok) return json(409, { error: "capacity ceiling reached" });
                        try {
                            return json(200, await engine.prepare(workerId, body));
                        } catch (error) {
                            // Release only when this worker holds no live record.
                            // A previous successful prepare owns its slot, and a
                            // failed re-prepare must not evict it.
                            const record = await engine.get(workerId);
                            if (!record || record.state === STATE.DELETED) {
                                await release(reservation.generation);
                            }
                            throw error;
                        }
                    }
                    if (request.method === "DELETE") {
                        let result;
                        try {
                            result = await engine.remove(workerId, await request.json());
                        } catch (error) {
                            // A retried delete whose first attempt released
                            // nothing: the deletion is already durable, so finish
                            // the cleanup instead of reporting a terminal record.
                            const record = await engine.get(workerId);
                            if (record?.state !== STATE.DELETED) throw error;
                            result = record;
                        }
                        // Release only after the deletion is durable: freeing the
                        // slot first would over-admit if the delete then failed.
                        // A repeated release is harmless.
                        await release(undefined);
                        return json(200, result);
                    }
                    if (request.method === "POST" && step === "start") {
                        return json(200, await engine.start(workerId, await request.json()));
                    }
                    return json(404, { error: "not found" });
                } catch (error) {
                    if (error instanceof ProtocolError) {
                        return json(error.status, { error: error.message });
                    }
                    return json(500, { error: "provider error" });
                }
            });
        } catch {
            return json(500, { error: "provider error" });
        }
    }
}

// ---------------------------------------------------------------------------
// Worker entry
// ---------------------------------------------------------------------------

export default {
    async fetch(request, env) {
        const url = new URL(request.url);
        if (url.pathname === "/healthz") return json(200, { ok: true });

        let settings;
        try {
            settings = readSettings(env);
        } catch {
            return json(503, { error: "provider is not configured" });
        }

        const authorization = request.headers.get("Authorization") ?? "";
        if (authorization !== `Bearer ${settings.token}`) {
            return json(401, { error: "unauthorized" });
        }

        // Capabilities must NOT require the scope header: the controller
        // discovers the scope from this response and sends none.
        if (url.pathname === "/v1/capabilities") {
            return json(200, profileFor(settings));
        }

        if (!url.pathname.startsWith("/v1/workers/")) return json(404, { error: "not found" });
        if (request.headers.get(SCOPE_HEADER) !== settings.scope) {
            return json(404, { error: "unknown scope" });
        }
        const workerId = url.pathname.slice("/v1/workers/".length).split("/")[0];
        if (!UUID_RE.test(workerId)) return json(404, { error: "not found" });

        // Capacity accounting lives with the record it protects: the ledger
        // reserves, writes, and rolls back in one serialized step.
        const ledger = env.WORKER_LEDGER.getByName(`${settings.scope}:${workerId}`);
        return ledger.fetch(request);
    },
};
