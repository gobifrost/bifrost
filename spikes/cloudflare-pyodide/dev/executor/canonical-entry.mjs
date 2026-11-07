// Guarded canonical transport candidate; no production/untrusted admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from '../runtime/runtime-code.mjs';
import {withCooperativeRpc} from '../../backend/cooperative-runtime.mjs';
import cooperativeSource from './cooperative-code.mjs';
import serializationSource from './rpc-serialization-code.mjs';
import {cooperativeRpcResponse} from '../../backend/cooperative-rpc-result.mjs';
import {boundedLosslessJson} from '../../backend/lossless-json.mjs';
import {admitCanonicalFixture} from './canonical-admission.mjs';
import {invocationStream} from '../../backend/invocation-stream.mjs';
import {terminalResponse} from '../../backend/terminal-response.mjs';
import {attemptPayload} from './policy.mjs';
import {canonicalApiFetch} from './canonical-api.mjs';
import {qualificationStatus} from './canonical-guard.mjs';
import {boundedJson} from '../../probe-io.mjs';

const attempts = new Map();
export class CanonicalApi extends WorkerEntrypoint {
  async fetch(request) {
    return canonicalApiFetch(request, this.ctx.props, this.env);
  }
}

export default {
  async fetch(request, env, ctx) {
    // Same temporary guard and expiry contract as the existing dev executor.
    const status = qualificationStatus(request, env);
    if (status !== null) return new Response('Private POST qualification only', {status});
    if (new URL(request.url).pathname === '/__fixture_warmup') {
      const response = await env.LOADER.load(runtimeCode(null)).getEntrypoint().fetch(
        new Request('https://warmup.invalid/', {method: 'POST', body: JSON.stringify({action: 'inspect'})}));
      return Response.json(await boundedJson(response));
    }
    let body;
    try {body = await boundedLosslessJson(request, 262144);}
    catch {return new Response('Invalid bounded request', {status: 400});}
    if (body?.mode !== 'canonical' || body.fixture_mode !== undefined) return new Response('Canonical fixed fixture only', {status: 422});
    try {return await admitCanonicalFixture(body, attempts.size, async () => {
      const id = crypto.randomUUID();
      const payload = {...attemptPayload(body, env.API_ORIGIN), action: 'execute_envelope', worker_id: body.worker_id};
      attempts.set(id, true);
      const cleanup = () => {attempts.delete(id);};
      const execute = async () => {
        const api = ctx.exports.CanonicalApi({props: {apiOrigin: env.API_ORIGIN,
          manifest: body.manifest, deadline: Date.parse(body.context.workflow_deadline)}});
        const code = withCooperativeRpc(runtimeCode(api), cooperativeSource, serializationSource);
        code.limits = {...code.limits, subRequests: 32};
        const entry = env.LOADER.load(code).getEntrypoint();
        const response = cooperativeRpcResponse(await entry.run_execution(JSON.stringify(payload)), body.execution_id);
        const terminal = await terminalResponse(response);
        const runtime_cleanup = {sdk_close_returned: response.headers.get('X-Spike-SDK-Close-Returned') === 'true'};
        if (terminal.oversized) return {runtime_handle: id, runtime_cleanup,
          result: {type: 'result', execution_id: body.execution_id, success: false,
            error_type: 'ExecutorBodyTooLargeError', error: 'Executor response exceeds retention budget; workflow effects may already exist'},
          provider_outcome: {kind: 'terminal_body_too_large', response_eof: true, engine_outcome_delivered: false,
            response_bytes: terminal.response_bytes, retention_limit_bytes: 1048576, drain_limit_bytes: 4194304}};
        return {runtime_handle: id, result: terminal.value, api_calls: [], runtime_cleanup,
          observation_scope: 'SDK persistence verified by canonical API; callback traces are not aggregated across isolates',
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
