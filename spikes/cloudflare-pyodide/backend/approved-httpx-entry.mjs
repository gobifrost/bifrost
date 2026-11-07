// Local transport prerequisite only; no canonical execution admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from './runtime-code.mjs';
import {createApprovedHttpsCandidate} from './approved-https-candidate.mjs';
import redirectPatch from './httpx-redirect-code.mjs';
const policies = new Map();
export class ApprovedHttpxGateway extends WorkerEntrypoint {
  fetch(request) {
    return policies.get(this.ctx.props.id)?.fetch(request) ?? new Response('Closed', {status: 410});
  }
}
const python = `
import hashlib, inspect
import httpx
import httpx._transports.jsfetch as transport
from workers import WorkerEntrypoint, Response
from runtime_guard import require_runtime
from httpx_redirect_patch import install
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        patch = install()
        rows = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for path in ("/write", "/redirect", "/same-origin"):
                result = await client.post("https://vendor.example" + path, content="synthetic",
                    headers={"Authorization": "Bearer vendor-token"})
                rows.append({"status": result.status_code, "history": [r.status_code for r in result.history],
                    "url": str(result.url), "body": result.text})
        return Response.json({"runtime": require_runtime(), "httpx": httpx.__version__,
            "transport_sha256": hashlib.sha256(inspect.getsource(transport).encode()).hexdigest(),
            "rows": rows, "patch": patch})
`;
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') return new Response('Local only', {status: 403});
    const id = crypto.randomUUID();
    const policy = createApprovedHttpsCandidate({origins: ['https://vendor.example'],
      maxRequests: 6, deadline: Date.now() + 30000, forward: async outbound => {
        const path = new URL(outbound.url).pathname;
        if (!['/write', '/redirect', '/same-origin', '/final'].includes(path)) return new Response('Fixed path only', {status: 403});
        return fetch(`http://127.0.0.1:8820${path}`, {method: outbound.method,
          headers: outbound.headers, body: outbound.method === 'GET' ? undefined : await outbound.text(), redirect: 'manual'});
      }});
    policies.set(id, policy);
    try {
      const code = runtimeCode(ctx.exports.ApprovedHttpxGateway({props: {id}}), python);
      code.modules['httpx_redirect_patch.py'] = {py: redirectPatch};
      code.limits.subRequests = 16;
      const response = await env.LOADER.load(code).getEntrypoint().fetch('https://synthetic.invalid/');
      return Response.json(await response.json());
    } finally {policy.close(); policies.delete(id);}
  },
};
