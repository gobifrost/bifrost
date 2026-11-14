// Loopback-only qualification bridge; never use as a deployed entrypoint.
import candidate from './canonical-entry.mjs';
import legacyFixture from '../../backend/authorized-local-entry.mjs';
import {WorkerEntrypoint} from 'cloudflare:workers';
import {canonicalApiFetch} from './canonical-api.mjs';
function localApi(outgoing) {
  const url = new URL(outgoing.url);
  if (url.origin !== 'https://test-api.invalid') throw new Error('Local API origin denied');
  const target = new URL('http://api:8000');
  target.pathname = url.pathname; target.search = url.search;
  return fetch(new Request(target, outgoing));
}
export class CanonicalApi extends WorkerEntrypoint {
  async fetch(request) {
    if (this.env.ISOLATED_TEST_STACK !== 'true') return new Response('Local test stack required', {status: 403});
    return canonicalApiFetch(request, this.ctx.props, {DEV_AUTH: 'synthetic-local-guard', DEV_API: {fetch: localApi}});
  }
}
let expiry;
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true') return new Response('Local test stack required', {status: 403});
    expiry ??= String(Date.now()+120000);
    if (new URL(request.url).pathname === '/__fixture_warmup') return legacyFixture.fetch(request, env, ctx);
    const headers = new Headers(request.headers);
    headers.set('X-Dev-Auth', 'synthetic-local-guard');
    return candidate.fetch(new Request(request, {headers}), {...env,
      DEV_AUTH: 'synthetic-local-guard', DEV_EXPIRES_AT: expiry, API_ORIGIN: 'https://test-api.invalid',
      DEV_API: {fetch: localApi},
    }, ctx);
  },
};
