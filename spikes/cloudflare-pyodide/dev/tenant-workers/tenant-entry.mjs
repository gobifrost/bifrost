// Per-tenant persistent Python worker supervisor (PROTOTYPE, local only).
//
// Pivot from fresh isolate per attempt (env.LOADER.load per attempt) to one
// long-lived generic worker PER TENANT via
// env.LOADER.get(<tenantWorkerId>, () => genericCode). Attempts for the same
// tenant are serialized through a per-tenant promise chain (Pyodide is
// single-threaded); different tenants run in different isolates.
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
import {genericWorkerCode, genericCodeDigest} from './generic-worker-code.mjs';
import {tenantWorkerId} from './tenant-registry.mjs';
import {capabilityRejection} from '../executor/capability-requirements.mjs';
import {egressRejection, isUrlAllowed} from '../executor/egress-allowlist.mjs';
import {reconcileImports, deniedTerminal} from '../executor/import-audit.mjs';
import {readBudgetedJson} from '../executor/output-budget.mjs';
import {DUMMY_CREDENTIAL} from '../executor/gateway-injection.mjs';

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

export default {
  async fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true') return new Response('Local prototype only', {status: 403});
    const url = new URL(request.url);
    if (request.method === 'GET' && url.pathname === '/__code_digest') {
      return Response.json({generic_code_sha256: await genericCodeDigest()});
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

    const workerReused = seenTenants.has(workerId);
    let outcome;
    try {
      outcome = await enqueueTenant(workerId, async () => {
      seenTenants.add(workerId);
      // One long-lived generic worker PER TENANT. The factory closes over NO
      // per-attempt data: the script is constant; workflow source travels as
      // attempt DATA below (body.modules/body.entry) into the virtual importer.
      const worker = env.LOADER.get(workerId, () => genericWorkerCode());
      const childBody = {action: body.action, marker: body.marker, ms: body.ms,
        modules: body.modules, entry: body.entry, credential: DUMMY_CREDENTIAL};
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
            api_calls: []}};
        }
        throw error;
      }
      if (body.action === 'run') {
        const audit = reconcileImports(body.requirements?.modules, output.recorded);
        const denied = deniedTerminal(audit);
        if (denied) return {terminal: {...denied, api_calls: []}, output};
      }
      return {output};
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
      queue_wait_ms: queueWaitMs, output: result.output, api_calls: []});
  },
};
