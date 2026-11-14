import packaged from './generated-modules.mjs';

// Reuses the existing engine probe; genuine run identity integration is separate.
const main = `
import json
from workers import WorkerEntrypoint, Response
from probe import run
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        body = json.loads(await request.text())
        return Response.json(await run(body["workload"], body.get("parameters")))
`;

export function freshEngineCode() {
  const modules = {};
  for(const [name,value] of Object.entries(packaged)) {
    modules[name] = value.base64 === undefined ? value : {
      data: Uint8Array.from(atob(value.base64),c=>c.charCodeAt(0)).buffer,
    };
  }
  modules['main.py'] = {py: main};
  return {compatibilityDate:'2026-10-30',compatibilityFlags:['python_workers'],
    mainModule:'main.py',modules,env:{},globalOutbound:null,limits:{cpuMs:1000,subRequests:4}};
}
