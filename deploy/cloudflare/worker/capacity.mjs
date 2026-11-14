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

function readSettings(env) {
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
        maxLifetimeSeconds: integer("MAX_LIFETIME_SECONDS", 60, 604800),
        image: text("WORKER_IMAGE", /^\S+$/),
        apiOrigin: text("API_ORIGIN", /^https:\/\/[^\s]+$/),
        startingGraceMs: integer("STARTING_GRACE_MS", 0, 600_000),
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

// The worker container's environment (API/DB/Redis/S3 selectors and the shared
// secret) is deployment configuration, supplied as a JSON object in one var so
// this class never hardcodes an environment.
function parseContainerEnv(env) {
    const raw = env.WORKER_CONTAINER_ENV;
    if (typeof raw !== "string" || raw.trim() === "") return {};
    try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
        throw new Error("WORKER_CONTAINER_ENV must be a JSON object");
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

    async live() {
        return (await this.ctx.storage.get("live")) ?? [];
    }

    async reserve(workerId, ceiling) {
        const live = await this.live();
        if (live.length >= ceiling) return false;
        if (!live.includes(workerId)) {
            live.push(workerId);
            await this.ctx.storage.put("live", live);
        }
        return true;
    }

    async release(workerId) {
        const live = (await this.live()).filter((id) => id !== workerId);
        await this.ctx.storage.put("live", live);
    }

    async fetch(request) {
        const url = new URL(request.url);
        if (url.pathname === "/live") return json(200, { live: await this.live() });
        if (url.pathname === "/reserve") {
            const body = await request.json();
            const ok = await this.reserve(body.worker_id, body.ceiling);
            return json(ok ? 200 : 409, { ok });
        }
        if (url.pathname === "/release") {
            const body = await request.json();
            await this.release(body.worker_id);
            return json(200, { ok: true });
        }
        return json(404, { error: "not found" });
    }
}

// ---------------------------------------------------------------------------
// Container runtime seam: the ONLY class that touches the Containers binding
// ---------------------------------------------------------------------------

export class WorkerContainer {
    constructor(ctx, env) {
        this.ctx = ctx;
        this.env = env;
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

    async fetch(request) {
        const url = new URL(request.url);
        try {
            if (url.pathname === "/start") {
                const body = await request.json();
                this.container.start({
                    env: body.env ?? {},
                    entrypoint: body.entrypoint ?? [],
                    enableInternet: true,
                });
                return json(200, { running: this.container.running === true });
            }
            if (url.pathname === "/destroy") {
                await this.container.destroy();
                return json(200, { running: false });
            }
            if (url.pathname === "/status") {
                // `running` is liveness, not admission: only the launcher's
                // signed hello proves a worker actually enrolled.
                return json(200, { running: this.container.running === true });
            }
            return json(404, { error: "not found" });
        } catch (error) {
            return json(500, { error: `${error.name}: ${error.message}`.slice(0, 200) });
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
        const stub = () => containers.getByName(workerId);
        return {
            async start(row) {
                const response = await stub().fetch("https://do/start", {
                    method: "POST",
                    body: JSON.stringify({
                        env: {
                            ...containerEnv,
                            BIFROST_WORKER_FORM: "external",
                            BIFROST_WORKER_INCARNATION_ID: row.worker_id,
                            BIFROST_WORKER_RESOURCE_UID: row.resource_uid,
                            BIFROST_POOL_WORKER_EXECUTION_LANE: "normal",
                        },
                        entrypoint: ["python", "-m", "src.worker.main"],
                    }),
                });
                if (!response.ok) throw new Error("container start failed");
                return { state: STATE.STARTING, handle: { container: workerId } };
            },
            async destroy() {
                await stub().fetch("https://do/destroy", { method: "POST" });
                return { state: STATE.DELETED };
            },
            async observe(row) {
                const response = await stub().fetch("https://do/status");
                const { running } = await response.json();
                if (running) return { state: STATE.RUNNING, handle: { container: workerId } };
                // Not running. If a start was ever attempted this incarnation is
                // terminal; otherwise it is still only prepared. The intent
                // marker covers a launch accepted before its handle persisted.
                const attempted = row.intent === "start" || row.started_at !== null;
                return {
                    state: attempted ? STATE.STOPPED : STATE.PREPARED,
                    handle: row.handle ?? {},
                };
            },
        };
    }

    engine(workerId) {
        const settings = this.settings;
        return createEngine({
            profile: {
                ...profileFor(settings),
                starting_grace_ms: settings.startingGraceMs,
            },
            store: this.store(workerId),
            runtime: this.runtime(workerId),
        });
    }

    async fetch(request) {
        const workerId = new URL(request.url).pathname.split("/")[3] ?? "";
        const engine = this.engine(workerId);
        const url = new URL(request.url);
        const step = url.pathname.split("/")[4] ?? "";
        try {
            if (url.pathname === "/capabilities") return json(200, profileFor(this.settings));
            if (request.method === "GET") return json(200, await engine.get(workerId));
            if (request.method === "PUT") return json(200, await engine.prepare(workerId, await request.json()));
            if (request.method === "DELETE") return json(200, await engine.remove(workerId, await request.json()));
            if (request.method === "POST" && step === "start") {
                return json(200, await engine.start(workerId, await request.json()));
            }
            return json(404, { error: "not found" });
        } catch (error) {
            if (error instanceof ProtocolError) return json(error.status, { error: error.message });
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
        if (authorization !== `Bearer ${env.CAPACITY_TOKEN}`) {
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

        const registry = env.SCOPE_REGISTRY.getByName(settings.scope);
        if (request.method === "PUT") {
            const reserved = await registry.fetch("https://do/reserve", {
                method: "POST",
                body: JSON.stringify({ worker_id: workerId, ceiling: settings.maxWorkers }),
            });
            if (!reserved.ok) return json(409, { error: "capacity ceiling reached" });
        }
        if (request.method === "DELETE") {
            await registry.fetch("https://do/release", {
                method: "POST",
                body: JSON.stringify({ worker_id: workerId }),
            });
        }

        const ledger = env.WORKER_LEDGER.getByName(`${settings.scope}:${workerId}`);
        return ledger.fetch(request);
    },
};
