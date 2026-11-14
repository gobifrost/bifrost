// Outbound gateway for engine-mode children, plus the token broker.
//
// The engine's virtual import and SDK calls go to https://test-api.invalid; this
// stub rewrites them to the configured API origin, strips cookies, and refuses
// any path outside the SDK capability surface (apiTarget).
//
// The child never holds the real credential: it carries an opaque handle
// (`run_<uuid>`) in context.engine_token. RunTokenBroker maps that handle to the
// attempt-scoped token, and TenantApi swaps it in on the way out. The handle is
// useless anywhere else and is deleted after the attempt.
import {DurableObject, WorkerEntrypoint} from 'cloudflare:workers';
import {apiTarget} from '../executor/policy.mjs';

const HANDLE_RE = /^run_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export class RunTokenBroker extends DurableObject {
  async fetch(request) {
    if (request.method === 'PUT') {
      let record;
      try {
        record = await request.json();
      } catch {
        return new Response('invalid record', {status: 400});
      }
      if (typeof record?.token !== 'string' || record.token.length < 10 ||
          typeof record?.expires !== 'number') {
        return new Response('invalid record', {status: 400});
      }
      await this.ctx.storage.put('record', record);
      return Response.json({ok: true});
    }
    if (request.method === 'DELETE') {
      await this.ctx.storage.deleteAll();
      return Response.json({ok: true});
    }
    const record = await this.ctx.storage.get('record');
    if (!record || Date.now() >= record.expires) {
      if (record) await this.ctx.storage.deleteAll();
      return new Response('unknown handle', {status: 404});
    }
    return Response.json({token: record.token});
  }
}

export class TenantApi extends WorkerEntrypoint {
  async fetch(request) {
    const authorization = request.headers.get('Authorization') ?? '';
    const handle = authorization.startsWith('Bearer ') ? authorization.slice(7) : '';
    if (!HANDLE_RE.test(handle)) {
      return new Response('opaque handle required', {status: 401});
    }
    let target;
    try {
      target = apiTarget(request.url, this.env.API_ORIGIN);
    } catch {
      return new Response('API capability denied', {status: 403});
    }
    const broker = this.env.TOKENS.get(this.env.TOKENS.idFromName(handle));
    const stored = await broker.fetch(new Request('https://token.invalid/'));
    if (stored.status !== 200) return new Response('unknown handle', {status: 401});
    const {token} = await stored.json();
    const headers = new Headers(request.headers);
    headers.set('Authorization', `Bearer ${token}`);
    headers.delete('cookie');
    return fetch(new Request(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: 'manual',
    }));
  }
}
