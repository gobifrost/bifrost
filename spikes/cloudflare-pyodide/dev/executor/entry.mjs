// Private, fixed synthetic qualification only. Not production dispatch admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from '../runtime/runtime-code.mjs';
import {boundedJson} from '../../probe-io.mjs';
import {verifyModuleResponse} from '../../backend/module-response.mjs';
import {verifyResolutionResponse} from '../../backend/resolution-response.mjs';
import {fixtureRejection} from '../../backend/fixture-admission.mjs';
import {apiTarget} from './policy.mjs';
import {pythonEntry} from './python-entry.mjs';

export class DevApi extends WorkerEntrypoint {
  async fetch(request) {
    let target;
    try { target = apiTarget(request.url, this.env.API_ORIGIN); }
    catch { return new Response('API capability denied', {status: 403}); }
    const headers = new Headers(request.headers);
    // A service-binding request carries the virtual origin's Host header.
    // Let fetch derive Host from the fixed physical destination.
    headers.delete('Host');
    headers.set('User-Agent', 'bifrost-pyodide-dev-executor/1.0');
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
    const calls = [];
    try {
      const api = ctx.exports.DevApi({props: {manifest: body.manifest}});
      const preflight = await api.fetch(new Request('https://test-api.invalid/api/sdk/modules/' + body.context.file_path,
        {headers: {Authorization: 'Bearer ' + body.context.engine_token}}));
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
        method: 'POST', body: JSON.stringify({action: 'execute',
          execution_id: body.execution_id, context: body.context, api_url: 'https://test-api.invalid'}),
      }));
      const payload = await boundedJson(response);
      return Response.json({result: payload.upstream_result, bootstrap: payload.bootstrap, api_calls: calls,
        api_calls_scope: 'parent_module_preflight_only'});
    } catch (error) {
      // Exception strings can contain workflow data or credentials.
      return Response.json({phase: 'dynamic-runtime', error_type: error.name, api_calls: calls}, {status: 502});
    }
  },
};
