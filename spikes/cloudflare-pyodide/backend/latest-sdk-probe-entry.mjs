import {runtimeCode} from './runtime-code.mjs';
import overlay from './sdk-overlay.mjs';
const python = `
from workers import WorkerEntrypoint, Response
from runtime_guard import require_runtime
import importlib
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        result = {"runtime": require_runtime(), "probes": {}}
        for name in ("ssl", "certifi", "bifrost.client"):
            try:
                module = importlib.import_module(name)
                result["probes"][name] = {"imported": True}
            except Exception as error:
                result["probes"][name] = {"imported": False, "error_type": type(error).__name__,
                    "missing_module": getattr(error, "name", None)}
        if result["probes"]["bifrost.client"]["imported"]:
            try:
                from bifrost.client import BifrostClient
                client = BifrostClient("https://unused.invalid", "synthetic-unusable-probe-value")
                result["constructor"] = {"created": True}
                client._sync_http.close()
            except Exception as error:
                result["constructor"] = {"created": False, "error_type": type(error).__name__}
        if result["probes"]["certifi"]["imported"]:
            import certifi, hashlib, ssl
            content = certifi.contents().encode()
            context = ssl.create_default_context(cafile=certifi.where())
            result["certifi_api"] = {"version": certifi.__version__,
                "contents_sha256": hashlib.sha256(content).hexdigest(),
                "contents_bytes": len(content), "certificate_store": context.cert_store_stats()}
        return Response.json(result)
`;
export default {
  async fetch(request, env) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') return new Response('Local probe only', {status: 403});
    const code = runtimeCode(null, python);
    for (const [name, value] of Object.entries(overlay.files)) {
      code.modules[name] = value.base64 === undefined ? value : {
        data: Uint8Array.from(atob(value.base64), character => character.charCodeAt(0)).buffer,
      };
    }
    const response = await env.LOADER.load(code).getEntrypoint().fetch(new Request('https://unused.invalid/'));
    // Fixed synthetic import/constructor probe; no workflow or network capability.
    return response;
  },
};
