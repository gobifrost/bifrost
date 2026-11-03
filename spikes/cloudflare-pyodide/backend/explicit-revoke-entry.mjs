// Isolated synthetic control link only, not public control authentication.
import diagnostic, {revokeDiagnosticCapability} from './https-disconnect-entry.mjs';
export {DisconnectGateway} from './https-disconnect-entry.mjs';
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true') return new Response('Local only', {status: 403});
    const url = new URL(request.url);
    if (request.method === 'POST' && url.pathname === '/revoke') {
      return Response.json(revokeDiagnosticCapability(url.searchParams.get('id')));
    }
    if (request.method === 'POST' && url.pathname === '/' && !url.search) {
      const body = await request.json();
      return diagnostic.fetch(new Request(`https://fixture.invalid/?id=${body.execution_id}`, {method: 'POST'}), env, ctx);
    }
    if (url.pathname === '/status') return diagnostic.fetch(request, env, ctx);
    return new Response('POST required', {status: 405});
  },
};
