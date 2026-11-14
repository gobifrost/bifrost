import {runtimeCode} from './runtime-code.mjs';
const python=`
from workers import WorkerEntrypoint, Response
from runtime_guard import require_runtime, check_runtime, RuntimeCompatibilityError
from runtime_runner import UnknownResources
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        observed = require_runtime()
        rejected = False
        effects = []
        try:
            check_runtime({**observed, "pyodide": "unqualified"})
            effects.append("must not execute")
        except RuntimeCompatibilityError:
            rejected = True
        return Response.json({"fingerprint": observed, "drift_rejected": rejected,
            "effects_after_rejection": effects,
            "upstream_worker_imported": UnknownResources().finish().peak_memory_bytes is None})
`;
export default {
  async fetch(request,env) {
    if(env.LOCAL_PROBE!=='true' || request.method!=='POST') return new Response('Local POST only',{status:403});
    return env.LOADER.load(runtimeCode(null,python)).getEntrypoint().fetch(new Request('https://guard.invalid/'));
  },
};
