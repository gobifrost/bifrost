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
        return Response.json(await run_authorized_attempt(body["execution_id"],body["context"],body["api_url"]))
`;

export function runtimeCode() {
  const modules={};
  for(const [name,value] of Object.entries(packaged)) {
    modules[name]=value.base64 === undefined ? value : {
      data:Uint8Array.from(atob(value.base64),c=>c.charCodeAt(0)).buffer,
    };
  }
  modules['main.py']={py:main};
  return {compatibilityDate:'2026-10-30',compatibilityFlags:['python_workers'],mainModule:'main.py',
    modules,env:{},globalOutbound:null,limits:{cpuMs:1000,subRequests:4}};
}
