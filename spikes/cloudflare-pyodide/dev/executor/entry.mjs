// Private, fixed synthetic qualification only. Not production dispatch admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from '../runtime/runtime-code.mjs';
import {boundedJson} from '../../probe-io.mjs';
import {verifyModuleResponse} from '../../backend/module-response.mjs';
import {verifyResolutionResponse} from '../../backend/resolution-response.mjs';
import {fixtureRejection} from '../../backend/fixture-admission.mjs';
import {apiTarget, attemptPayload} from './policy.mjs';
import {pythonEntry} from './python-entry.mjs';
import {readBudgetedJson} from './output-budget.mjs';
import {reconcileImports, deniedTerminal} from './import-audit.mjs';
import {capabilityRejection} from './capability-requirements.mjs';
import {egressRejection, VIRTUAL_TEST_HOST} from './egress-allowlist.mjs';
import {decideGatewayAction, sanitizedChildContext, injectionHeaders, externalHeaders,
  DUMMY_CREDENTIAL} from './gateway-injection.mjs';

export class DevApi extends WorkerEntrypoint {
  async fetch(request) {
    const egress = this.ctx.props.egress;
    const runToken = this.ctx.props.runToken;
    // Egress gate BEFORE any network effect. Declared hosts plus the virtual
    // test endpoint only. Anything else fails closed with zero api_calls and
    // never echoes hostnames or secrets; only the shape class is named.
    // Reuses egress-allowlist logic (test-api.invalid + declared hosts only).
    const decision = decideGatewayAction(request.url, egress);
    if (decision.action === 'reject') {
      return Response.json({phase: 'egress-rejected', reason: decision.reason, api_calls: []}, {status: 422});
    }
    const hostname = new URL(request.url).hostname.toLowerCase();
    if (decision.action === 'forward-external' || hostname !== VIRTUAL_TEST_HOST) {
      // Declared external host: direct HTTPS fetch, no DEV_API bridge.
      // Strip X-Dev-Auth/X-Qualification-Auth/Cookie always.
      const headers = externalHeaders(request.headers);
      return fetch(new Request(request.url, new Request(request, {headers, redirect: 'manual'})));
    }
    // test-api.invalid: inject run-scoped auth held ONLY in parent env/props.
    // Child env carries NO tokens (dummy tolerated because gateway injects real).
    // Strip incoming auth/cookies/qualification headers; never trust child bearer.
    if (typeof runToken !== 'string' || runToken.length < 10 || runToken === DUMMY_CREDENTIAL) {
      return Response.json({phase: 'egress-rejected', reason: 'unlisted', api_calls: []}, {status: 422});
    }
    let target;
    try { target = apiTarget(request.url, this.env.API_ORIGIN); }
    catch { return new Response('API capability denied', {status: 403}); }
    const headers = injectionHeaders(request.headers, runToken, this.env.DEV_AUTH ?? '');
    const response = await this.env.DEV_API.fetch(new Request(target, new Request(request, {headers, redirect: 'manual'})));
    const path = new URL(request.url).pathname;
    if (path.startsWith('/api/sdk/modules/')) {
      const module = decodeURIComponent(path.slice('/api/sdk/modules/'.length));
      return verifyModuleResponse(response, module, this.ctx.props.manifest[module]);
    }
    if (path === '/api/sdk/modules-resolve') return verifyResolutionResponse(response, this.ctx.props.manifest);
    return response;
  }
}
export default {
  async fetch(request, env, ctx) {
    if (!env.DEV_AUTH || request.headers.get('X-Dev-Auth') !== env.DEV_AUTH ||
        !(Date.now() < Number(env.DEV_EXPIRES_AT))) return new Response('Private qualification only', {status: 403});
    if (request.method !== 'POST') return new Response('POST required', {status: 405});
    const body = await boundedJson(request, 262144);
    const rejection = fixtureRejection(body, 0);
    if (rejection || body.fixture_mode !== undefined || body.mode !== undefined) return Response.json({error: 'Fixture not admitted', reason: rejection ?? 'unsupported_mode'}, {status: 422});
    // Capability gate BEFORE any loader, network, or SDK effect. Manifest
    // lookup only. Missing declarations fail closed with zero api_calls and
    // never echo secrets or params; only the missing module names are named.
    const capability = capabilityRejection(body.requirements, body.manifest);
    if (capability) return Response.json({phase: 'capability-rejected',
      missing_modules: capability.missing_modules, api_calls: []}, {status: 422});
    // Egress gate BEFORE any loader, network, or SDK effect. Declaration
    // rides the attempt. Absent egress keeps old behavior (test-api.invalid
    // only). Invalid shape fails closed with zero api_calls and echoes only
    // the shape class, never hostnames or secrets.
    const egress = egressRejection(body.egress);
    if (egress) return Response.json({phase: 'egress-rejected',
      reason: egress.reason, api_calls: []}, {status: 422});
    // Gateway injection: run-scoped auth held ONLY in parent props.
    // Child receives sanitized context with dummy (non-token-shaped) placeholder
    // tolerated by the SDK because the gateway injects the real bearer.
    // Tech debt: refresh via gateway not yet implemented; bounded runs stay
    // within token lifetime so no refresh occurs.
    const runToken = body.context?.engine_token;
    if (typeof runToken !== 'string' || runToken.length < 10) {
      return Response.json({phase: 'egress-rejected', reason: 'unlisted', api_calls: []}, {status: 422});
    }
    const childContext = sanitizedChildContext(body.context);
    const childBody = {...body, context: childContext};
    let payload;
    try { payload = attemptPayload(childBody, env.API_ORIGIN); }
    catch { return new Response('Invalid dev public URL configuration', {status: 503}); }
    const calls = [];
    try {
      const api = ctx.exports.DevApi({props: {manifest: body.manifest, egress: body.egress, runToken}});
      // Preflight via gateway with dummy bearer; gateway injects real auth.
      const preflight = await api.fetch(new Request('https://test-api.invalid/api/sdk/modules/' + body.context.file_path,
        {headers: {Authorization: 'Bearer ' + DUMMY_CREDENTIAL}}));
      const moduleStatus = preflight.status;
      calls.push({path: '/api/sdk/modules/' + body.context.file_path, method: 'GET', status: moduleStatus});
      await preflight.body?.cancel();
      if (moduleStatus !== 200) return Response.json({phase: 'module-preflight', module_status: moduleStatus,
        content_type: preflight.headers.get('Content-Type'), server: preflight.headers.get('Server'), api_calls: calls}, {status: 502});
      const code = runtimeCode(api, pythonEntry);
      // The fixed SDK workflow needs source resolution, context, configuration,
      // table discovery/creation and a document write. Keep a bounded dev cap.
      code.limits = {...code.limits, subRequests: 12};
      const child = env.LOADER.load(code);
      const response = await child.getEntrypoint().fetch(new Request('https://attempt.invalid/', {
        method: 'POST', body: JSON.stringify(payload),
      }));
      const output = await readBudgetedJson(response);
      // Deny undeclared workspace imports: the run executed, so this maps the
      // terminal post-hoc (effects may already exist) instead of banking Success.
      // Clean runs keep the exact prior envelope shape.
      const audit = reconcileImports(body.requirements?.modules, output.bootstrap?.recorded_workspace_paths);
      const denied = deniedTerminal(audit);
      if (denied) return Response.json({...denied, api_calls: calls}, {status: 502});
      return Response.json({result: output.upstream_result, bootstrap: output.bootstrap,
        ...(audit ? {import_audit: audit} : {}), api_calls: calls,
        api_calls_scope: 'parent_module_preflight_only'});
    } catch (error) {
      if (error?.name === 'ExecutorBodyTooLargeError') return Response.json({phase: 'output-budget',
        error_type: 'ExecutorBodyTooLargeError',
        error: 'Executor response exceeds retention budget; workflow effects may already exist',
        provider_outcome: {kind: 'terminal_body_too_large', response_eof: true, engine_outcome_delivered: false,
          response_bytes: error.observedBytes, retention_limit_bytes: error.limitBytes},
        api_calls: calls}, {status: 502});
      // Exception strings can contain workflow data or credentials.
      return Response.json({phase: 'dynamic-runtime', error_type: error.name, api_calls: calls}, {status: 502});
    }
  },
};
