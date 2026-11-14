import {WorkerEntrypoint} from 'cloudflare:workers';
import {enforcementWorkerCode} from './hostile-enforcement-code.mjs';

// Hostile enforcement supervisor (Phase 2). Fresh LOADER.load per attempt,
// never get()/cache. No Bifrost credentials, no business queue.
// ALLOWED covers the five Phase-2 probes plus an honest baseline.
const ALLOWED = new Set([
  'honest',
  'cpu_catch_effect',
  'cpu_nocatch',
  'mem_grow',
  'subrequest_abuse',
  'output_abuse',
  'normal_accounting',
]);

const observations = new Map();

export class Effects extends WorkerEntrypoint {
  record(event) {
    const value = String(event);
    if (!['before', 'caught', 'after'].includes(value)) throw new TypeError('Invalid probe event');
    const obs = observations.get(this.ctx.props.id);
    if (obs) obs.push(value);
  }
}

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
    const id = crypto.randomUUID();
    const records = [];
    observations.set(id, records);
    try {
      const code = enforcementWorkerCode(`enforce-${id}`, ctx.exports.Effects({props: {id}}));
      const worker = env.LOADER.load(code);
      let outcome;
      const wallStart = Date.now();
      try {
        const running = worker.getEntrypoint().fetch(new Request('https://attempt.invalid/', {
          method: 'POST', body: JSON.stringify({action: body.action, marker: body.marker ?? id}),
        }));
        const response = await running;
        // For output_abuse, do not blindly JSON-parse a 2MB body into logs;
        // record status + bounded size, then attempt a bounded parse.
        const rawText = await response.text();
        let parsed;
        try { parsed = JSON.parse(rawText); } catch { parsed = {unparsed_len: rawText.length, unparsed_head: rawText.slice(0, 500)}; }
        // Redact huge payloads from the supervisor envelope: keep length only.
        if (parsed && typeof parsed.big === 'string' && parsed.big.length > 1024) {
          parsed = {big_len: parsed.big.length, len: parsed.len, truncated_for_safety: true};
        }
        outcome = {status: response.status, body: parsed, raw_len: rawText.length};
      } catch (error) {
        outcome = {error_type: error.name, error: String(error).slice(0, 2000)};
      }
      const wallMs = Date.now() - wallStart;
      return Response.json({revision: 1, outcome, effects: records,
        wall_ms: wallMs,
        parent_canary_configured: env.PARENT_CANARY === 'synthetic-supervisor-only'});
    } catch (error) {
      return Response.json({revision: 1, outcome: {error_type: error.name, error: String(error).slice(0, 2000)}}, {status: 502});
    } finally {
      observations.delete(id);
    }
  },
};
