import {boundedJson} from '../../probe-io.mjs';
import {forwardSignalProbe} from '../../backend/request-signal-probe.mjs';
export const SIGNAL_CASES = ['completion', 'parent_abort', 'io_abort', 'cpu_abort'];
export async function writeSignalLog(api, token, caseId, event) {
  if (!SIGNAL_CASES.includes(caseId) || !['preflight', 'before', 'caught', 'after', 'parent_abort_received'].includes(event)) {
    throw new TypeError('Fixed signal event only');
  }
  const response = await api.fetch(new Request('https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev/api/sdk/runtime/logs', {
    method: 'POST', redirect: 'manual', headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
    body: JSON.stringify({level: 'INFO', message: `signal:${caseId}:${event}`}),
  }));
  await response.body?.cancel();
  return response.status;
}
export async function handleSignalProbe(request, env, effects, waitUntil) {
  if (!env.DEV_AUTH || request.headers.get('X-Dev-Auth') !== env.DEV_AUTH || !(Date.now() < Number(env.DEV_EXPIRES_AT))) {
    return new Response('Private qualification only', {status: 403});
  }
  if (request.method !== 'POST') return new Response('POST required', {status: 405});
  let body;
  try {
    body = await boundedJson(request, 8192);
    if (!body || Object.keys(body).sort().join(',') !== 'case_id,run_token' || !SIGNAL_CASES.includes(body.case_id) ||
        typeof body.run_token !== 'string' || body.run_token.length < 16 || body.run_token.length > 4096 || /[\r\n]/.test(body.run_token)) {
      throw new TypeError('Fixed input required');
    }
  } catch { return new Response('Fixed signal diagnostic only', {status: 422}); }
  const log = event => writeSignalLog(env.DEV_API, body.run_token, body.case_id, event).then(status => {
    if (status !== 200) throw new Error('Independent observer rejected event');
  });
  try {
    const status = await writeSignalLog(env.DEV_API, body.run_token, body.case_id, 'preflight');
    if (status !== 200) return Response.json({phase: 'observer-auth', observer_status: status}, {status: 502});
    if (['completion', 'parent_abort'].includes(body.case_id)) {
      request.signal.addEventListener('abort', () => waitUntil(log('parent_abort_received')), {once: true});
      await log('before');
      await new Promise(resolve => setTimeout(resolve, body.case_id === 'completion' ? 100 : 2000));
      await log('after');
      return Response.json({parent_only: true});
    }
    const forwarded = new Request('https://fixed-signal.invalid/', {method: 'POST', signal: request.signal,
      body: JSON.stringify({id: crypto.randomUUID(), mode: body.case_id})});
    return await forwardSignalProbe(forwarded, env.LOADER, () => effects({token: body.run_token, caseId: body.case_id}),
      () => log('parent_abort_received'), waitUntil);
  } catch (error) {
    return Response.json({phase: 'signal-probe', error_type: error.name, physical_termination_verified: false}, {status: 502});
  }
}
