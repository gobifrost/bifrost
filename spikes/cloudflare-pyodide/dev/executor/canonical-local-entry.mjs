// Loopback-only qualification bridge; never use as a deployed entrypoint.
import candidate from './canonical-entry.mjs';
import legacyFixture from '../../backend/authorized-local-entry.mjs';
export {CanonicalApi} from './canonical-entry.mjs';
const expiry = String(Date.now()+120000);
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true') return new Response('Local test stack required', {status: 403});
    if (new URL(request.url).pathname === '/__fixture_warmup') return legacyFixture.fetch(request, env, ctx);
    const headers = new Headers(request.headers);
    headers.set('X-Dev-Auth', 'synthetic-local-guard');
    return candidate.fetch(new Request(request, {headers}), {...env,
      DEV_AUTH: 'synthetic-local-guard', DEV_EXPIRES_AT: expiry, API_ORIGIN: 'https://test-api.invalid',
      DEV_API: {fetch: outgoing => {
        const url = new URL(outgoing.url);
        if (url.origin !== 'https://test-api.invalid') throw new Error('Local API origin denied');
        return fetch(new Request('http://api:8000'+url.pathname+url.search, outgoing));
      }},
    }, ctx);
  },
};
