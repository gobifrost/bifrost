// Credential-free local-workerd qualification. Never deploy this entrypoint.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {createApprovedHttpsCandidate} from './approved-https-candidate.mjs';

const policies = new Map();
export class ApprovedHttpsGateway extends WorkerEntrypoint {
  fetch(request) {
    const policy = policies.get(this.ctx.props.id);
    return policy ? policy.fetch(request) : new Response('Unknown attempt', {status: 403});
  }
}
const python = `
import json, sys, sysconfig
from workers import WorkerEntrypoint, Response
from pyodide.http import pyfetch
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        observed = []
        for url, headers in (
            ("https://other.example/write", {}),
            ("https://vendor.example/write", {"X-Test": "synthetic-run-token"}),
            ("https://vendor.example/write", {"Authorization": "Bearer vendor-token"}),
            ("https://vendor.example/redirect", {}),
            ("https://vendor.example/write", {}),
        ):
            response = await pyfetch(url, method="POST", headers=headers, body="synthetic", redirect="manual")
            observed.append({"status": response.status, "body": await response.string()})
        return Response(json.dumps({"observed": observed, "python": sys.version,
            "abi": sysconfig.get_config_var("SOABI")}))
`;
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') {
      return new Response('Local POST only', {status: 403});
    }
    const id = crypto.randomUUID();
    const policy = createApprovedHttpsCandidate({origins: ['https://vendor.example'],
      forbiddenCredentials: ['synthetic-run-token'], maxRequests: 2,
      deadline: Date.now() + 30000, forward: async outbound => {
        const path = new URL(outbound.url).pathname;
        if (!['/write', '/redirect'].includes(path)) return new Response('Fixed path only', {status: 403});
        return fetch(`http://127.0.0.1:8820${path}`, {method: 'POST',
          headers: outbound.headers, body: await outbound.text(), redirect: outbound.redirect});
      }});
    policies.set(id, policy);
    try {
      const child = env.LOADER.load({compatibilityDate: '2026-10-30',
        compatibilityFlags: ['python_workers'], mainModule: 'main.py',
        modules: {'main.py': {py: python}}, env: {},
        globalOutbound: ctx.exports.ApprovedHttpsGateway({props: {id}}),
        limits: {cpuMs: 1000, subRequests: 16}});
      const response = await child.getEntrypoint().fetch('https://synthetic.invalid/');
      return Response.json(await response.json());
    } finally {
      policy.close(); policies.delete(id);
    }
  },
};
