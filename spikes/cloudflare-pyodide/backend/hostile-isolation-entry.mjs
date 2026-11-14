import {WorkerEntrypoint} from 'cloudflare:workers';
import {hostileWorkerCode} from './hostile-isolation-code.mjs';

// Hostile Phase-1 supervisor. Fixed diagnostic only: fresh LOADER.load per
// attempt, never get()/cache. No Bifrost credentials, no business queue.
const ALLOWED = new Set([
  'honest','plant_builtins','read_builtins','plant_module','read_module',
  'plant_env','read_env','plant_fs','read_fs','plant_creds','read_creds',
  'plant_concurrent','read_concurrent','import_plant','import_read',
]);

export default {
  async fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true' && (!env.SPIKE_AUTH ||
        !(Date.now() < Number(env.SPIKE_EXPIRES_AT)) ||
        request.headers.get('X-Spike-Auth') !== env.SPIKE_AUTH)) {
      return new Response('Private diagnostic only', {status: 403});
    }
    if (request.method !== 'POST') return new Response('POST required', {status: 405});
    const body = await request.json();
    if (!ALLOWED.has(body.action)) return new Response('Unsupported', {status: 422});
    if (typeof body.marker !== 'undefined' && (typeof body.marker !== 'string' || body.marker.length > 128)) {
      return new Response('Invalid marker', {status: 400});
    }
    // plant_creds requires an explicit per-attempt secret; read_creds must not receive one.
    if (body.action === 'plant_creds' && typeof body.secret !== 'string') {
      return new Response('Secret required', {status: 400});
    }
    if (body.action === 'read_creds' && typeof body.secret !== 'undefined') {
      return new Response('read_creds takes no secret', {status: 400});
    }
    const id = crypto.randomUUID();
    try {
      const withHelper = body.action.startsWith('import_');
      const secret = body.action === 'plant_creds' ? body.secret : null;
      const code = hostileWorkerCode(`hostile-${id}`, null, secret, withHelper);
      // Fresh interpreter per attempt. Never use cached get(id).
      const worker = env.LOADER.load(code);
      let outcome;
      try {
        const running = worker.getEntrypoint().fetch(new Request('https://attempt.invalid/', {
          method: 'POST', body: JSON.stringify({action: body.action, marker: body.marker ?? id}),
        }));
        const response = await running;
        outcome = {status: response.status, body: await response.json()};
      } catch (error) {
        outcome = {error_type: error.name, error: String(error)};
      }
      return Response.json({revision: 1, outcome,
        parent_canary_configured: env.PARENT_CANARY === 'synthetic-supervisor-only'});
    } catch (error) {
      return Response.json({revision: 1, outcome: {error_type: error.name, error: String(error)}}, {status: 502});
    }
  },
};
