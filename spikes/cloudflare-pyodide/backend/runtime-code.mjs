import packaged from './generated-runtime.mjs';

const main=`
import json
from workers import WorkerEntrypoint, Response
from runtime_runner import run_authorized_attempt

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        body = json.loads(await request.text())
        if body["action"] == "inspect":
            from src.services.execution.worker import ResourceMetrics
            from src.core.module_cache_sync import mark_execution_child
            return Response.json({"worker_imported": True, "http_module_path_available": callable(mark_execution_child),
                                  "unknown_metrics": ResourceMetrics(None,None,None,None,None).__dict__})
        if body["action"] == "sync_transport":
            from src.core.module_cache_sync import _get_http_client
            try:
                response = _get_http_client().get("https://example.com")
                return Response.json({"status": response.status_code})
            except Exception as exc:
                return Response.json({"error_type": type(exc).__name__, "error": str(exc)})
        if body["action"] == "invalid_credential":
            # Negative real-API test, not a claimed authorized execution.
            context = {"caller":{"user_id":"00000000-0000-0000-0000-000000000002",
                                  "email":"probe@example.invalid","name":"Probe"},
                       "name":"unavailable-fixture", "function_name":"workflow",
                       "file_path":"workflows/unavailable-fixture.py",
                       "engine_token":"synthetic-invalid-runtime-token",
                       "transient":True, "no_cache":True}
            return Response.json(await run_authorized_attempt(
                "00000000-0000-0000-0000-000000000001",context,"https://local-api.invalid"))
        return Response.json(await run_authorized_attempt(body["execution_id"],body["context"],body["api_url"]))
`;

export function runtimeCode(outbound=null) {
  const modules={};
  for(const [name,value] of Object.entries(packaged)) {
    modules[name]=value.base64 === undefined ? value : {
      data:Uint8Array.from(atob(value.base64),c=>c.charCodeAt(0)).buffer,
    };
  }
  modules['main.py']={py:main};
  return {compatibilityDate:'2026-10-30',compatibilityFlags:['python_workers'],mainModule:'main.py',
    modules,env:{},globalOutbound:outbound,limits:{cpuMs:1000,subRequests:4}};
}
