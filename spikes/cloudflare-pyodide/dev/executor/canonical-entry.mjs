// Guarded canonical transport candidate; no production/untrusted admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from '../runtime/runtime-code.mjs';
import {withCooperativeRpc} from '../../backend/cooperative-runtime.mjs';
import cooperativeSource from './cooperative-code.mjs';
import serializationSource from './rpc-serialization-code.mjs';
import {cooperativeRpcResponse} from '../../backend/cooperative-rpc-result.mjs';
import {boundedLosslessJson} from '../../backend/lossless-json.mjs';
import {admitCanonicalFixture} from './canonical-admission.mjs';
import {createAttemptEgress} from '../../backend/attempt-egress.mjs';
import {invocationStream} from '../../backend/invocation-stream.mjs';
import {terminalResponse} from '../../backend/terminal-response.mjs';
import {verifyModuleResponse} from '../../backend/module-response.mjs';
import {verifyResolutionResponse} from '../../backend/resolution-response.mjs';
import {sourceIntegrityResponse} from '../../backend/source-integrity-response.mjs';
import {logDeliveryEvidence} from '../../backend/log-delivery-evidence.mjs';
import {apiTarget, attemptPayload} from './policy.mjs';
import {qualificationStatus} from './canonical-guard.mjs';

const attempts = new Map();
export class CanonicalApi extends WorkerEntrypoint {
  async fetch(request) {
    const slot = attempts.get(this.ctx.props.id);
    if (!slot) return new Response('Attempt capability closed', {status: 410});
    let target;
    try {target = apiTarget(request.url, slot.apiOrigin);}
    catch {return new Response('API capability denied', {status: 403});}
    const response = await slot.egress.fetch(new Request(target, request));
    const path = new URL(request.url).pathname;
    const observation = {method: request.method, path, status: response.status};
    slot.calls.push(observation);
    if (path.startsWith('/api/sdk/modules/')) {
      const module = decodeURIComponent(path.slice('/api/sdk/modules/'.length));
      return sourceIntegrityResponse(() => verifyModuleResponse(response, module, slot.manifest[module]), observation);
    }
    if (path === '/api/sdk/modules-resolve') {
      return sourceIntegrityResponse(() => verifyResolutionResponse(response, slot.manifest), observation);
    }
    return response;
  }
}

export default {
  async fetch(request, env, ctx) {
    // Same temporary guard and expiry contract as the existing dev executor.
    const status = qualificationStatus(request, env);
    if (status !== null) return new Response('Private POST qualification only', {status});
    let body;
    try {body = await boundedLosslessJson(request, 262144);}
    catch {return new Response('Invalid bounded request', {status: 400});}
    if (body?.mode !== 'canonical' || body.fixture_mode !== undefined) return new Response('Canonical fixed fixture only', {status: 422});
    try {return await admitCanonicalFixture(body, attempts.size, async () => {
      const id = crypto.randomUUID();
      const payload = {...attemptPayload(body, env.API_ORIGIN), action: 'execute_envelope', worker_id: body.worker_id};
      const egress = createAttemptEgress(Date.parse(body.context.workflow_deadline), outgoing => {
        const headers = new Headers(outgoing.headers);
        headers.delete('Host'); headers.set('User-Agent', 'bifrost-pyodide-dev-executor/1.0');
        return env.DEV_API.fetch(new Request(outgoing, {headers, redirect: 'manual'}));
      });
      const slot = {egress, calls: [], manifest: body.manifest, apiOrigin: env.API_ORIGIN};
      attempts.set(id, slot);
      const cleanup = () => {egress.close(); attempts.delete(id);};
      const execute = async () => {
        const api = ctx.exports.CanonicalApi({props: {id}});
        const code = withCooperativeRpc(runtimeCode(api), cooperativeSource, serializationSource);
        code.limits = {...code.limits, subRequests: 12};
        const entry = env.LOADER.load(code).getEntrypoint();
        const response = cooperativeRpcResponse(await entry.run_execution(JSON.stringify(payload)), body.execution_id);
        const terminal = await terminalResponse(response);
        const runtime_cleanup = {sdk_close_returned: response.headers.get('X-Spike-SDK-Close-Returned') === 'true'};
        const log_delivery = logDeliveryEvidence(terminal.value?.logs, slot.calls);
        if (terminal.oversized) return {runtime_handle: id, runtime_cleanup,
          ...(log_delivery ? {log_delivery} : {}),
          result: {type: 'result', execution_id: body.execution_id, success: false,
            error_type: 'ExecutorBodyTooLargeError', error: 'Executor response exceeds retention budget; workflow effects may already exist'},
          provider_outcome: {kind: 'terminal_body_too_large', response_eof: true, engine_outcome_delivered: false,
            response_bytes: terminal.response_bytes, retention_limit_bytes: 1048576, drain_limit_bytes: 4194304}};
        return {runtime_handle: id, result: terminal.value, api_calls: slot.calls, runtime_cleanup,
          ...(log_delivery ? {log_delivery} : {}),
          ...(response.headers.get('X-Spike-RPC-Result-Too-Large') === 'true' ? {provider_outcome: {
            kind: 'rpc_result_too_large', execute_returned: true, engine_outcome_delivered: false, rpc_wire_limit_bytes: 4194304}} : {})};
      };
      if (request.headers.get('Accept') === 'application/x-ndjson') {
        return invocationStream({runtimeHandle: id, executionId: body.execution_id, execute, cleanup});
      }
      try {return Response.json(await execute());}
      finally {cleanup();}
    });} catch {return Response.json({phase: 'canonical-runtime', error_type: 'ProviderError'}, {status: 502});}
  },
};
