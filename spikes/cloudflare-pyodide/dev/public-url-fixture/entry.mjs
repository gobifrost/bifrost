// Fixed context conformance only: unused synthetic credential, no SDK API calls.
import {runtimeCode} from './runtime-code.mjs';
import {pythonEntry} from './python-entry.mjs';
import {attemptPayload} from './policy.mjs';
const script = `import os
from bifrost import context
result = {'public_url': context.public_url, 'transport_url': os.environ['BIFROST_API_URL'],
          'execution_id': context.execution_id, 'parent_secret_present': 'BIFROST_SECRET_KEY' in os.environ}
`;
export default {
  async fetch(request, env) {
    if (env.LOCAL_PROBE !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const records = [];
    for (const mode of ['legacy', 'explicit']) {
      const body = {execution_id: '00000000-0000-0000-0000-000000000001',
        public_url: 'https://attacker.invalid', api_url: 'https://attacker.invalid',
        context: {code: btoa(script), name: 'fixed-public-url-conformance',
          caller: {user_id: '00000000-0000-0000-0000-000000000002', email: 'probe@example.invalid', name: 'Probe'},
          engine_token: 'synthetic-invalid-unused-token', transient: true, no_cache: true, timeout_seconds: 10}};
      const payload = attemptPayload(body, env.PUBLIC_URL);
      if (mode === 'legacy') payload.public_url = null;
      const response = await env.LOADER.load(runtimeCode(null, pythonEntry)).getEntrypoint().fetch(
        new Request('https://attempt.invalid/', {method: 'POST', body: JSON.stringify(payload)}));
      records.push({mode, ...await response.json()});
    }
    const abi = records[1].bootstrap.abi;
    return Response.json({abi: {python: abi.python, pyodide: abi.pyodide, soabi: abi.soabi}, records,
      sdk_api_calls: 0, genuine_run_authorization_qualified: false,
      physical_termination_verified: false, cloud_invocations: 0});
  },
};
