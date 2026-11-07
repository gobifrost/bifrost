import {boundedJson} from '../../probe-io.mjs';
import {CPU_CASES, runCpuPlacement} from '../../backend/cpu-placement.mjs';

export function cpuProbeInput(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body) ||
      Object.keys(body).sort().join(',') !== 'case_id,run_token' ||
      !CPU_CASES.includes(body.case_id) || typeof body.run_token !== 'string' ||
      body.run_token.length < 16 || body.run_token.length > 4096 || /[\r\n]/.test(body.run_token)) {
    throw new TypeError('Fixed CPU diagnostic only');
  }
  return {caseId: body.case_id, token: body.run_token};
}

export async function handleCpuProbe(request, env, makeEffects) {
  if (!env.DEV_AUTH || request.headers.get('X-Dev-Auth') !== env.DEV_AUTH ||
      !(Date.now() < Number(env.DEV_EXPIRES_AT))) return new Response('Private qualification only', {status: 403});
  if (request.method !== 'POST') return new Response('POST required', {status: 405});
  let input;
  try { input = cpuProbeInput(await boundedJson(request, 8192)); }
  catch { return new Response('Fixed CPU diagnostic only', {status: 422}); }
  try {
    // Verify live run authority before creating a billable Python child. This
    // parent event is distinct from the child's before/caught/after events.
    const response = await env.DEV_API.fetch(new Request(
      'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev/api/sdk/runtime/logs', {
        method: 'POST', redirect: 'manual', headers: {Authorization: `Bearer ${input.token}`, 'Content-Type': 'application/json'},
        body: JSON.stringify({level: 'INFO', message: `cpu-placement:${input.caseId}:parent-preflight`}),
      }));
    const status = response.status;
    await response.body?.cancel();
    if (status !== 200) return Response.json({phase: 'observer-auth', observer_status: status}, {status: 502});
    const outcome = await runCpuPlacement(env.LOADER, makeEffects(input), input.caseId);
    return Response.json({runtime: 'deployed-cloudflare', outcome, physical_termination_verified: false});
  } catch (error) {
    return Response.json({phase: 'cpu-probe', error_type: error.name, physical_termination_verified: false}, {status: 502});
  }
}
