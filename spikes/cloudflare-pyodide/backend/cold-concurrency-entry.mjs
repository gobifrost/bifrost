// Local-only provider reproducer: no BiFrost, packages, secrets or outbound.
const python=`
from workers import WorkerEntrypoint, Response
import builtins
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        before = getattr(builtins, "_cold_probe", None)
        builtins._cold_probe = "written"
        return Response.json({"before": before, "value": 42})
`;
export function definition() {
  return {compatibilityDate:'2026-10-30',compatibilityFlags:['python_workers'],
    mainModule:'main.py',modules:{'main.py':{py:python}},env:{},globalOutbound:null,
    limits:{cpuMs:1000,subRequests:0}};
}
export async function pair(loader) {
  return Promise.all([0,1].map(async index=>{
    try {
      const worker=loader.load(definition());
      const response=await worker.getEntrypoint().fetch(new Request('https://probe.invalid/'));
      // Fixed tiny response generated above; no user code or request input.
      return {index,status:response.status,body:await response.json()};
    } catch(error) {return {index,error_type:error.name,error:String(error.message).slice(0,1024)};}
  }));
}
export default {
  async fetch(request,env) {
    if(env.LOCAL_REPRO!=='true') return new Response('Local fixture only',{status:403});
    if(request.method!=='POST') return new Response('POST required',{status:405});
    return Response.json({participants:await pair(env.LOADER)});
  },
};
