// Reference Bifrost execution-capacity provider for Cloudflare Containers.
//
// The API is the canonical owner of demand, admission, claims, progress,
// cancellation, and recovery (PostgreSQL). This worker only prepares, starts,
// and deletes container incarnations addressed by exact resource UID. It never
// receives job payloads and never chooses which jobs execute.
//
// Protocol: docs/architecture/external-worker-capacity.md and
// api/shared/models.py (WorkerCapacityProfile/Reservation/Resource).
// Only the `default` export below touches Cloudflare; every rule above it is
// pure, dependency-free, and covered by capacity.test.mjs.

const SCOPE_RE = /^[a-zA-Z0-9._:-]+$/;
const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

const MIN_MEMORY_BYTES = 64 * 1024 * 1024;
const MAX_MEMORY_BYTES = 128 * 1024 * 1024 * 1024;
const MIN_LIFETIME_SECONDS = 60;
const MAX_LIFETIME_SECONDS = 7 * 24 * 60 * 60;

function timingSafeEqual(a, b) {
    if (typeof a !== "string" || typeof b !== "string") return false;
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return diff === 0;
}

function json(status, body) {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
}

const unauthorized = () => json(401, { error: "unauthorized" });
const badRequest = (error) => json(400, { error });
const notFound = () => json(404, { error: "unknown worker" });
const conflict = (error) => json(409, { error });

function validReservation(body) {
    if (typeof body !== "object" || body === null) return "reservation must be an object";
    const { worker_id, worker_version, memory_limit_bytes, max_lifetime_seconds, drain_grace_seconds } = body;
    if (typeof worker_id !== "string" || !UUID_RE.test(worker_id)) return "worker_id must be a UUID";
    if (typeof worker_version !== "string" || worker_version.length === 0 || worker_version.length > 100) {
        return "worker_version must be 1-100 characters";
    }
    if (!Number.isInteger(memory_limit_bytes) || memory_limit_bytes < MIN_MEMORY_BYTES ||
        memory_limit_bytes > MAX_MEMORY_BYTES || memory_limit_bytes % (1024 * 1024) !== 0) {
        return "memory_limit_bytes out of range";
    }
    if (!Number.isInteger(max_lifetime_seconds) || max_lifetime_seconds < MIN_LIFETIME_SECONDS ||
        max_lifetime_seconds > MAX_LIFETIME_SECONDS) {
        return "max_lifetime_seconds out of range";
    }
    if (!Number.isInteger(drain_grace_seconds) || drain_grace_seconds < 0 || drain_grace_seconds > 3600) {
        return "drain_grace_seconds out of range";
    }
    return null;
}

function resourceUidFor(workerId) {
    return `res-${workerId.toLowerCase()}`;
}

function toResource(record) {
    return {
        scope: record.scope,
        worker_id: record.worker_id,
        worker_version: record.worker_version,
        resource_uid: record.resource_uid,
        state: record.state,
        out_of_memory: false,
    };
}

function liveCount(store) {
    let n = 0;
    for (const record of store.values()) {
        if (record.state === "prepared" || record.state === "starting" || record.state === "running") n++;
    }
    return n;
}

export function createCapacityProvider({ settings, store, runtime }) {
    const profile = () => ({
        protocol_version: 1,
        scope: settings.scope,
        worker_version: settings.workerVersion,
        memory_limit_bytes: settings.memoryLimitBytes,
        max_workers: settings.maxWorkers,
        max_lifetime_seconds: settings.maxLifetimeSeconds,
    });

    async function handle(request) {
        const supplied = request.headers.get("Authorization") ?? "";
        const expected = `Bearer ${settings.token}`;
        if (!timingSafeEqual(supplied, expected)) return unauthorized();

        const url = new URL(request.url);
        if (!url.pathname.startsWith("/v1/")) return notFound();
        const scope = request.headers.get("X-Bifrost-Capacity-Scope");
        if (scope !== settings.scope || !SCOPE_RE.test(scope ?? "")) {
            return badRequest("unknown capacity scope");
        }

        const parts = url.pathname.slice("/v1/".length).split("/");
        if (request.method === "GET" && parts.length === 1 && parts[0] === "capabilities") {
            return json(200, profile());
        }
        if (parts.length < 2 || parts[0] !== "workers" || !UUID_RE.test(parts[1] ?? "")) return notFound();
        const workerId = parts[1].toLowerCase();
        const record = store.get(workerId) ?? null;
        const sameScope = (r) => r !== null && r.scope === scope;

        if (request.method === "GET" && parts.length === 2) {
            if (!sameScope(record)) return notFound();
            return json(200, toResource(record));
        }

        if (request.method === "PUT" && parts.length === 2) {
            let body = null;
            try {
                body = await request.json();
            } catch {
                return badRequest("reservation must be JSON");
            }
            const invalid = validReservation(body);
            if (invalid) return badRequest(invalid);
            if (body.worker_id.toLowerCase() !== workerId) return badRequest("reservation targets another worker");
            if (body.worker_version !== settings.workerVersion) {
                return conflict("worker build does not match provider version");
            }
            if (record !== null) {
                if (!sameScope(record)) return notFound();
                const same = record.worker_version === body.worker_version &&
                    record.memory_limit_bytes === body.memory_limit_bytes &&
                    record.max_lifetime_seconds === body.max_lifetime_seconds &&
                    record.drain_grace_seconds === body.drain_grace_seconds;
                // Never resurrect a stopped incarnation; never relabel a live one.
                if (!same || record.state === "stopped" || record.state === "deleted") {
                    return conflict("conflicting reservation for an existing worker");
                }
                return json(200, toResource(record));
            }
            if (liveCount(store) >= settings.maxWorkers) {
                return json(429, { error: "provider at capacity" });
            }
            const created = {
                scope,
                worker_id: workerId,
                worker_version: body.worker_version,
                memory_limit_bytes: body.memory_limit_bytes,
                max_lifetime_seconds: body.max_lifetime_seconds,
                drain_grace_seconds: body.drain_grace_seconds,
                resource_uid: resourceUidFor(workerId),
                state: "prepared",
            };
            store.set(workerId, created);
            return json(200, toResource(created));
        }

        if (request.method === "POST" && parts.length === 3 && parts[2] === "start") {
            let body = null;
            try {
                body = await request.json();
            } catch {
                return badRequest("start requires a resource_uid");
            }
            if (!sameScope(record)) return notFound();
            if (typeof body?.resource_uid !== "string" || body.resource_uid !== record.resource_uid) {
                return conflict("resource UID does not match the prepared incarnation");
            }
            if (record.state === "stopped" || record.state === "deleted") {
                return conflict("a stopped incarnation is never restarted");
            }
            if (record.state === "prepared") {
                await runtime.launchWorker({
                    name: `bifrost-${record.worker_id}`,
                    image: settings.image,
                    env: {
                        BIFROST_WORKER_FORM: "external",
                        BIFROST_WORKER_INCARNATION_ID: record.worker_id,
                        BIFROST_WORKER_RESOURCE_UID: record.resource_uid,
                        BIFROST_API_URL: settings.apiOrigin,
                    },
                });
                record.state = "starting";
            }
            return json(200, toResource(record));
        }

        if (request.method === "DELETE" && parts.length === 2) {
            let body = null;
            try {
                body = await request.json();
            } catch {
                return badRequest("delete requires a resource_uid");
            }
            if (!sameScope(record)) return notFound();
            if (typeof body?.resource_uid !== "string" || body.resource_uid !== record.resource_uid) {
                return conflict("resource UID does not match the prepared incarnation");
            }
            if (record.state !== "deleted") {
                await runtime.stopWorker(`bifrost-${record.worker_id}`);
                record.state = "deleted";
            }
            return json(200, toResource(record));
        }

        return notFound();
    }

    return { fetch: handle, profile };
}

// Wrangler entrypoint. Settings come from wrangler.toml [vars] plus the
// CAPACITY_TOKEN secret; nothing is read from requests. The container seam
// (`runtime`) is the ONLY Cloudflare-coupled call below: wire it to the
// Containers binding for the pinned image (see the runbook). All protocol
// rules above are binding-independent and covered by capacity.test.mjs.
function readSettings(env) {
    const text = (name, pattern) => {
        const value = env[name];
        if (typeof value !== "string" || value.trim() !== value || !pattern.test(value)) {
            throw new Error(`Explicit valid ${name} is required`);
        }
        return value;
    };
    const integer = (name, min, max) => {
        const value = typeof env[name] === "string" && /^\d+$/.test(env[name]) ? Number(env[name]) : NaN;
        if (!Number.isInteger(value) || value < min || value > max) {
            throw new Error(`Explicit valid ${name} is required`);
        }
        return value;
    };
    const apiOrigin = text("API_ORIGIN", /^https:\/\/[^/?#\s]+$/);
    return {
        token: text("CAPACITY_TOKEN", /^[!-~]{32,512}$/),
        scope: text("SCOPE", /^[a-zA-Z0-9._:-]{1,128}$/),
        workerVersion: text("WORKER_VERSION", /^.{1,100}$/),
        memoryLimitBytes: integer("MEMORY_LIMIT_BYTES", MIN_MEMORY_BYTES, MAX_MEMORY_BYTES),
        maxWorkers: integer("MAX_WORKERS", 1, 1000),
        maxLifetimeSeconds: integer("MAX_LIFETIME_SECONDS", MIN_LIFETIME_SECONDS, MAX_LIFETIME_SECONDS),
        image: text("WORKER_IMAGE", /^\S+$/),
        apiOrigin,
    };
}

// Reference store. Module scope, so records survive across requests handled by
// the same isolate — a per-request Map would drop a prepared reservation before
// its start request arrives. A production provider MUST persist records (for
// example in a Durable Object) so idempotency and tombstones survive restarts.
// See the runbook's "Limits" section.
const store = new Map();

export default {
    async fetch(request, env) {
        let settings = null;
        try {
            settings = readSettings(env);
        } catch {
            return json(503, { error: "provider is not configured" });
        }
        const runtime = {
            async launchWorker() {
                throw new Error(
                    "Wire launchWorker/stopWorker to the Containers binding for the pinned image " +
                    "(docs/runbooks/cloudflare-containers.md).",
                );
            },
            async stopWorker() {
                throw new Error(
                    "Wire launchWorker/stopWorker to the Containers binding for the pinned image " +
                    "(docs/runbooks/cloudflare-containers.md).",
                );
            },
        };
        return createCapacityProvider({ settings, store, runtime }).fetch(request);
    },
};
