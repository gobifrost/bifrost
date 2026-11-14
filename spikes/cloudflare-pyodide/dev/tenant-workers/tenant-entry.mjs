// Per-tenant persistent Python worker supervisor (PROTOTYPE, local only).
//
// Pivot from fresh isolate per attempt (env.LOADER.load per attempt) to one
// long-lived generic worker PER TENANT via
// env.LOADER.get(<tenantWorkerId>, () => genericCode). Attempts for the same
// tenant are serialized through a per-tenant promise chain (Pyodide is
// single-threaded); different tenants run in different isolates.
//
// Module source path (PATH-keyed, gateway-mediated): the attempt supplies
// body.modules as storage path -> source and body.manifest as storage path ->
// sha256. The supervisor never forwards the raw store: every byte the child
// receives comes out of createTenantGateway responses to
// GET /api/sdk/modules/<path> (pin-verified) after
// GET /api/sdk/modules-resolve resolves the entry, matching how dev/executor
// fetches. Recorded workspace loads are therefore REAL storage paths and the
// import audit reconciles paths. The gateway holds the run token
// (body.context?.engine_token) in props only; callers present DUMMY_CREDENTIAL.
//
// Security posture (reused, not reimplemented):
// - capability-requirements.mjs: undeclared modules rejected BEFORE any
//   loader/network/SDK effect.
// - egress-allowlist.mjs + gateway-injection.mjs decideGatewayAction: egress
//   gate BEFORE any loader/network effect; child holds NO credentials (child
//   env is {} and attempt bodies carry only DUMMY_CREDENTIAL).
// - output-budget.mjs: oversized child output dropped as infra failure.
// - import-audit.mjs: recorded workspace paths reconciled post-hoc; undeclared
//   imports map to a terminal (effects may already exist, same as executor).
import {genericWorkerCode, genericCodeDigest, tenantEngineCode} from './generic-worker-code.mjs';
import {tenantWorkerId} from './tenant-registry.mjs';
import {createTenantGateway, createRemoteTenantGateway, isStoragePath} from './tenant-gateway.mjs';
import {capabilityRejection} from '../executor/capability-requirements.mjs';
import {egressRejection, isUrlAllowed} from '../executor/egress-allowlist.mjs';
import {reconcileImports, deniedTerminal} from '../executor/import-audit.mjs';
import {readBudgetedJson} from '../executor/output-budget.mjs';
import {DUMMY_CREDENTIAL} from '../executor/gateway-injection.mjs';

// Node unit tests cannot resolve cloudflare:workers, so the export is loaded
// only where the runtime provides it; ctx.exports cannot see it otherwise.
let TenantApi;
let RunTokenBroker;
try {
  ({TenantApi, RunTokenBroker} = await import('./tenant-api.mjs'));
} catch {
  TenantApi = undefined;
  RunTokenBroker = undefined;
}
export {TenantApi, RunTokenBroker};

// Per-tenant serialization chains + first-use tracking. Module-level: one
// supervisor isolate holds one chain per tenant id.
const tails = new Map();
const seenTenants = new Set();

export function resetTenantState() {
  tails.clear();
  seenTenants.clear();
}

// Serializes fn behind the tenant's chain. Resolves with {result, queueWaitMs}
// where queueWaitMs is the time spent waiting for the previous attempt on the
// same tenant. A rejection in one attempt never breaks the chain for later ones.
export async function enqueueTenant(tenantId, fn) {
  const prev = tails.get(tenantId) ?? Promise.resolve();
  const enqueuedAt = Date.now();
  const cur = prev.then(async () => {
    const queueWaitMs = Date.now() - enqueuedAt;
    return {result: await fn(), queueWaitMs};
  });
  tails.set(tenantId, cur.catch(() => {}));
  return cur;
}

function json(status, value) {
  return Response.json(value, {status});
}

// Attempt store as a record (storage path -> source); anything else behaves
// as an empty store so the gateway preflight fails closed after the gates.
function gatewaySources(modules) {
  return modules !== null && typeof modules === 'object' && !Array.isArray(modules) ? modules : {};
}

export default {
  async fetch(request, env, ctx) {
    // Local workerd sets LOCAL_PROBE=true. A deployed instance instead needs the
    // spike auth header inside an unexpired window (same shape as the other
    // spike entries); anything else fails closed before any tenant work.
    if (env.LOCAL_PROBE !== 'true' &&
        (!env.SPIKE_AUTH || request.headers.get('X-Spike-Auth') !== env.SPIKE_AUTH ||
         !(Date.now() < Number(env.SPIKE_EXPIRES_AT)))) {
      return new Response('Local prototype only', {status: 403});
    }
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/__code_digest') {
      return Response.json({generic_code_sha256: await genericCodeDigest()});
    }
    if (request.method === 'GET' && url.pathname === '/__gateway_probe') {
      // Diagnostic: can this Worker reach the configured API origin at all?
      const origin = typeof env.API_ORIGIN === 'string' ? env.API_ORIGIN : '';
      if (!origin) return Response.json({configured: false});
      try {
        const response = await fetch(`${origin}/health/ready`, {redirect: 'manual'});
        return Response.json({configured: true, status: response.status});
      } catch (error) {
        return Response.json({configured: true, error_type: error?.name ?? 'Error',
          error: String(error?.message ?? error).slice(0, 200)});
      }
    }
    if (request.method !== 'POST') return new Response('POST required', {status: 405});
    let body;
    try {
      body = await request.json();
    } catch {
      return json(400, {phase: 'bad-request', error: 'Invalid JSON'});
    }

    let workerId;
    try {
      workerId = await tenantWorkerId(body.tenant_id);
    } catch {
      return json(400, {phase: 'bad-request', error: 'Invalid tenant_id'});
    }

    // Per-attempt posture control (diagnostic only): LOADER.load with no id
    // bills one Dynamic Worker per invocation, unlike the per-tenant
    // LOADER.get below. Never used by tenant routing; used to measure the
    // creation-meter difference in the billed account.
    if (body.action === 'legacy_load') {
      const worker = env.LOADER.load(genericWorkerCode());
      const response = await worker.getEntrypoint().fetch(new Request('https://attempt.invalid/', {
        method: 'POST', body: JSON.stringify({action: 'honest', credential: DUMMY_CREDENTIAL}),
      }));
      return Response.json({mode: 'legacy_load', output: await readBudgetedJson(response)});
    }

    // Capability gate BEFORE any loader/network/SDK effect.
    const capability = capabilityRejection(body.requirements, body.manifest);
    if (capability) {
      return json(422, {phase: 'capability-rejected',
        missing_modules: capability.missing_modules, api_calls: []});
    }
    // Egress gate BEFORE any loader/network/SDK effect.
    const egress = egressRejection(body.egress);
    if (egress) {
      return json(422, {phase: 'egress-rejected', reason: egress.reason, api_calls: []});
    }
    if (body.url !== undefined && !isUrlAllowed(body.url, body.egress)) {
      return json(422, {phase: 'egress-rejected', reason: 'unlisted', api_calls: []});
    }

    // Run entries are storage paths (e.g. 'workflows/wf.py'), validated BEFORE
    // any loader/network/SDK effect alongside the capability/egress gates.
    if (body.action === 'run' && !isStoragePath(body.entry)) {
      return json(400, {phase: 'bad-request', error: 'Invalid entry path', api_calls: []});
    }

    const workerReused = seenTenants.has(workerId);
    let outcome;
    try {
      outcome = await enqueueTenant(workerId, async () => {
      seenTenants.add(workerId);
      // One long-lived generic worker PER TENANT. The factory closes over NO
      // per-attempt data: the script is constant. Workflow source travels as
      // PATH-keyed DATA below, but only bytes the TenantGateway served after
      // manifest-pin verification (never the raw attempt store).
      const apiCalls = [];
      let childBody;
      if (body.action === 'engine') {
        // Real-engine mode: constant child per tenant, outbound through the
        // stateless TenantApi stub. The engine authenticates SDK calls with the
        // attempt-scoped token in context (posture note in dev/staging/README).
        if (typeof TenantApi !== 'function') {
          return {terminal: {phase: 'engine-unavailable', api_calls: []}};
        }
        const api = ctx.exports.TenantApi({});
        const code = tenantEngineCode(api);
        const engineId = `tenant-eng-${workerId}`;
        const worker = env.LOADER.get(engineId, () => code);
        // The child carries only an opaque handle; the attempt token lives in
        // the Durable Object and is deleted after the attempt.
        const handle = `run_${crypto.randomUUID()}`;
        const broker = env.TOKENS.get(env.TOKENS.idFromName(handle));
        await broker.fetch(new Request('https://token.invalid/', {
          method: 'PUT',
          body: JSON.stringify({token: body.context.engine_token,
            expires: Date.now() + 10 * 60 * 1000}),
        }));
        let response;
        try {
          response = await worker.getEntrypoint().fetch(new Request('https://attempt.invalid/', {
            method: 'POST',
            body: JSON.stringify({execution_id: body.execution_id,
              context: {...body.context, engine_token: handle},
              api_url: 'https://test-api.invalid',
              public_url: body.public_url ?? 'https://staging.invalid'}),
          }));
        } finally {
          await broker.fetch(new Request('https://token.invalid/', {method: 'DELETE'}));
        }
        const output = await readBudgetedJson(response);
        return {output: output.upstream_result, apiCalls};
      }
      if (body.action === 'run') {
        // Staging mode: fetch through the real API with the run token held in
        // the parent. The attempt declares the module closure; anything outside
        // it is never fetched.
        const remote = typeof env.API_ORIGIN === 'string' && env.API_ORIGIN !== '';
        const gateway = remote
          ? createRemoteTenantGateway({apiOrigin: env.API_ORIGIN, manifest: body.manifest,
              runToken: body.context?.engine_token})
          : createTenantGateway({store: body.modules, manifest: body.manifest,
              runToken: body.context?.engine_token});
        const gatewayGet = async (path) => {
          const response = await gateway.fetch(new Request(`https://test-api.invalid${path}`, {
            headers: {Authorization: `Bearer ${DUMMY_CREDENTIAL}`},
          }));
          apiCalls.push({path, method: 'GET', status: response.status});
          return response;
        };
        // Resolve the entry through the gateway first (mirrors executor's
        // modules-resolve + modules/<path> preflight): declared modules must
        // resolve in the manifest BEFORE the loader effect below.
        const resolveName = body.entry.slice(0, -'.py'.length).replaceAll('/', '.');
        const resolvedResponse = await gatewayGet(
          `/api/sdk/modules-resolve?name=${encodeURIComponent(resolveName)}`);
        const resolved = await resolvedResponse.json();
        if ((resolved.kind !== 'module' && resolved.kind !== 'package') ||
            resolved.storage_path !== body.entry) {
          return {terminal: {phase: 'module-preflight', module_status: resolvedResponse.status,
            path: body.entry, resolution: resolved.kind, api_calls: apiCalls}};
        }
        // Fetch every declared path through the gateway. Local mode keeps the
        // attempt-supplied store (plus extra loadable paths for the audit);
        // remote mode fetches exactly the declared closure from the real API.
        const modulePaths = remote
          ? [...new Set((body.requirements?.modules ?? []).filter(id => isStoragePath(id)))]
          : Object.keys(gatewaySources(body.modules));
        const fetched = {};
        for (const path of modulePaths) {
          const moduleResponse = await gatewayGet(`/api/sdk/modules/${path}`);
          if (moduleResponse.status !== 200) {
            return {terminal: {phase: 'module-preflight', module_status: moduleResponse.status,
              path, api_calls: apiCalls}};
          }
          const served = await moduleResponse.json();
          fetched[served.path] = served.content;
        }
        if (!(body.entry in fetched)) {
          return {terminal: {phase: 'module-preflight', module_status: 404,
            path: body.entry, api_calls: apiCalls}};
        }
        childBody = {action: body.action, entry: body.entry,
          modules: fetched, credential: DUMMY_CREDENTIAL};
      } else {
        childBody = {action: body.action, marker: body.marker, ms: body.ms,
          credential: DUMMY_CREDENTIAL};
      }
      const worker = env.LOADER.get(workerId, () => genericWorkerCode());
      const response = await worker.getEntrypoint().fetch(new Request('https://attempt.invalid/', {
        method: 'POST', body: JSON.stringify(childBody),
      }));
      let output;
      try {
        output = await readBudgetedJson(response);
      } catch (error) {
        if (error?.name === 'ExecutorBodyTooLargeError') {
          return {terminal: {phase: 'output-budget', error_type: 'ExecutorBodyTooLargeError',
            error: 'Executor response exceeds retention budget; workflow effects may already exist',
            provider_outcome: {kind: 'terminal_body_too_large', response_eof: true,
              engine_outcome_delivered: false,
              response_bytes: error.observedBytes, retention_limit_bytes: error.limitBytes},
            api_calls: apiCalls}};
        }
        throw error;
      }
      if (body.action === 'run') {
        const audit = reconcileImports(body.requirements?.modules, output.recorded);
        const denied = deniedTerminal(audit);
        if (denied) return {terminal: {...denied, api_calls: apiCalls}, output};
      }
      return {output, apiCalls};
      });
    } catch (error) {
      // Never echo exception strings (may contain workflow data or credentials).
      return json(502, {phase: 'dynamic-runtime', error_type: error?.name ?? 'Error',
        tenant_worker_id: workerId, api_calls: []});
    }
    const {result, queueWaitMs} = outcome;

    if (result?.terminal) {
      return json(502, {...result.terminal, tenant_worker_id: workerId,
        queue_wait_ms: queueWaitMs});
    }
    return Response.json({tenant_worker_id: workerId, worker_reused: workerReused,
      queue_wait_ms: queueWaitMs, output: result.output, api_calls: result.apiCalls ?? []});
  },
};
