// Outbound gateway for engine-mode children.
//
// The engine's virtual import and SDK calls go to https://test-api.invalid; this
// stub rewrites them to the configured API origin, strips cookies, and refuses
// any path outside the SDK capability surface (apiTarget). It is stateless, so
// it is safe to embed in a cached per-tenant worker.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {apiTarget} from '../executor/policy.mjs';

export class TenantApi extends WorkerEntrypoint {
  async fetch(request) {
    let target;
    try {
      target = apiTarget(request.url, this.env.API_ORIGIN);
    } catch {
      return new Response('API capability denied', {status: 403});
    }
    const headers = new Headers(request.headers);
    headers.delete('cookie');
    return fetch(new Request(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: 'manual',
    }));
  }
}
