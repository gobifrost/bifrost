// Disposable Cloudflare conformance entrypoint.
//
// This module is never included in the production capacity deployment.  It
// adds one narrowly-authenticated fault seam to a temporary conformance Worker
// while reusing the production ledger, registry, and container lifecycle.

import capacityProvider, {
    ScopeRegistry,
    WorkerContainer as ProductionWorkerContainer,
    WorkerLedger,
    readSettings,
} from "../../../cloudflare/worker/capacity.mjs";

const SCOPE_HEADER = "X-Bifrost-Capacity-Scope";
const CONTROL_HEADER = "Authorization";
const STOP_PATH = /^\/__conformance\/v1\/workers\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\/stop$/;
const CONTAINER_STOP_PATH = "/__conformance/stop";
const RESOURCE_UID_RE = /^[a-zA-Z0-9._:-]{1,255}$/;

const json = (status, body) => new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
});

function conformanceSettings(env) {
    const settings = readSettings(env);
    const token = env.CONFORMANCE_CONTROL_TOKEN;
    if (typeof token !== "string" || !/^[^\s]{32,512}$/.test(token)) {
        throw new Error("invalid or missing CONFORMANCE_CONTROL_TOKEN");
    }
    return { scope: settings.scope, token };
}

function authorized(request, settings) {
    return request.headers.get(CONTROL_HEADER) === `Bearer ${settings.token}`
        && request.headers.get(SCOPE_HEADER) === settings.scope;
}

async function binding(request) {
    try {
        const body = await request.json();
        if (
            !body
            || typeof body !== "object"
            || Array.isArray(body)
            || Object.keys(body).length !== 1
            || typeof body.resource_uid !== "string"
            || !RESOURCE_UID_RE.test(body.resource_uid ?? "")
        ) {
            return null;
        }
        return body.resource_uid;
    } catch {
        return null;
    }
}

// Cloudflare binds this class in the disposable conformance Worker.  Its
// inherited fetch() serializes lifecycle access and restores the production
// monitor; only this exact test route differs from production behavior.
export class WorkerContainer extends ProductionWorkerContainer {
    async handle(request) {
        const url = new URL(request.url);
        if (url.pathname !== CONTAINER_STOP_PATH) return super.handle(request);
        if (request.method !== "POST") return json(405, { error: "method not allowed" });

        let settings;
        try {
            settings = conformanceSettings(this.env);
        } catch {
            return json(503, { error: "conformance control is not configured" });
        }
        if (!authorized(request, settings)) return json(401, { error: "unauthorized" });

        const resourceUid = await binding(request);
        if (resourceUid === null) return json(400, { error: "invalid resource binding" });
        const requested = { worker_id: request.headers.get("X-Bifrost-Conformance-Worker"), resource_uid: resourceUid };
        if (!STOP_PATH.test(`/__conformance/v1/workers/${requested.worker_id}/stop`)) {
            return json(400, { error: "invalid worker binding" });
        }

        let fact;
        try {
            fact = await this.lifecycle(requested, { allowMissing: false });
        } catch {
            return json(409, { error: "container lifecycle does not match the incarnation" });
        }
        if (!fact.start_requested || fact.destroyed || fact.exit || !this.container.running) {
            return json(409, { error: "container incarnation is not running" });
        }

        try {
            // Do not call the provider's /destroy path.  The existing monitor
            // records this as an exit, so the ledger reaches stopped and keeps
            // its no-restart fence without a provider tombstone or slot release.
            await this.container.destroy();
        } catch {
            return json(502, { error: "container destroy failed" });
        }
        return json(202, { lifecycle: "stop_requested" });
    }
}

export { ScopeRegistry, WorkerLedger };

export default {
    async fetch(request, env) {
        const url = new URL(request.url);
        const match = STOP_PATH.exec(url.pathname);
        if (!match) return capacityProvider.fetch(request, env);

        let settings;
        try {
            settings = conformanceSettings(env);
        } catch {
            return json(503, { error: "conformance control is not configured" });
        }
        if (request.method !== "POST") return json(405, { error: "method not allowed" });
        if (!authorized(request, settings)) return json(401, { error: "unauthorized" });

        const resourceUid = await binding(request);
        if (resourceUid === null) return json(400, { error: "invalid resource binding" });
        const workerId = match[1];
        const target = env.WORKER_CONTAINER;
        if (!target || typeof target.getByName !== "function") {
            return json(503, { error: "container binding is not configured" });
        }
        return target.getByName(workerId).fetch("https://do" + CONTAINER_STOP_PATH, {
            method: "POST",
            headers: {
                [CONTROL_HEADER]: `Bearer ${settings.token}`,
                [SCOPE_HEADER]: settings.scope,
                "X-Bifrost-Conformance-Worker": workerId,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ resource_uid: resourceUid }),
        });
    },
};
