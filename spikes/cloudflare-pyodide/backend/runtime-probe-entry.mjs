import {runtimeCode} from './runtime-code.mjs';
const python=`
from workers import WorkerEntrypoint, Response
import sys, sysconfig, pyodide
import pydantic, pydantic_core
from pydantic import BaseModel, ValidationError
class Probe(BaseModel):
    values: list[int]
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        good = Probe.model_validate({"values": ["1", 2]}).model_dump()
        rejected = False
        try:
            Probe.model_validate({"values": ["invalid"]})
        except ValidationError:
            rejected = True
        return Response.json({"pyodide": pyodide.__version__, "python": list(sys.version_info[:3]),
            "cache_tag": sys.implementation.cache_tag, "soabi": sysconfig.get_config_var("SOABI"),
            "platform": sysconfig.get_platform(), "pydantic": pydantic.__version__,
            "pydantic_core": pydantic_core.__version__, "validated": good, "invalid_rejected": rejected})
`;
export default {
  async fetch(request,env) {
    if(env.LOCAL_PROBE!=='true' || request.method!=='POST') return new Response('Local POST only',{status:403});
    return env.LOADER.load(runtimeCode(null,python)).getEntrypoint().fetch(new Request('https://probe.invalid/'));
  },
};
