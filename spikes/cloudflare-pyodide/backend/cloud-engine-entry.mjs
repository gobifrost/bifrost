// Fixed current-engine cloud fixtures, synthetic context only. No SDK identity.
import {runtimeCode} from './runtime-code.mjs';
const main=`
import asyncio
import json
import logging
from dataclasses import asdict
from workers import WorkerEntrypoint, Response
from runtime_runner import run_authorized_attempt
from src.services.execution.engine import ExecutionRequest, execute
from bifrost._execution_context import Caller, ExecutionContext

async def transform(context: ExecutionContext, count=100):
    return {"total":sum(range(count)),"public_url":context.public_url,"count":count}

async def asynchronous(context: ExecutionContext):
    await asyncio.sleep(0.01)
    return {"async":True,"execution_id":context.execution_id}

async def failure():
    logging.getLogger("workflow").info("fixed cloud diagnostic log")
    raise ValueError("fixed cloud diagnostic error")

class Default(WorkerEntrypoint):
    async def fetch(self,request):
        body=json.loads(await request.text())
        functions={"transform":transform,"async":asynchronous,"failure":failure}
        result=await execute(ExecutionRequest(
            execution_id="00000000-0000-0000-0000-000000000001",
            caller=Caller("00000000-0000-0000-0000-000000000002","probe@example.invalid","Probe"),
            organization=None,func=functions[body["fixture"]],name=body["fixture"],
            public_url="https://isolated.example.invalid",transient=True,no_cache=True))
        return Response.json(asdict(result))
`;
export default {
  async fetch(request,env) {
    if(!env.SPIKE_AUTH || request.headers.get('X-Spike-Auth')!==env.SPIKE_AUTH ||
       !(Date.now()<Number(env.SPIKE_EXPIRES_AT))) return new Response('Private diagnostic only',{status:403});
    if(request.method!=='POST') return new Response('POST required',{status:405});
    const body=await request.json();
    if(!['transform','async','failure'].includes(body.fixture)) return new Response('Unsupported',{status:422});
    try {
      return await env.LOADER.load(runtimeCode(null,main)).getEntrypoint().fetch(new Request('https://attempt.invalid/',{
        method:'POST',body:JSON.stringify({fixture:body.fixture}),
      }));
    } catch(error) {
      return Response.json({phase:'dynamic-runtime',error_type:error.name,error:String(error)},{status:502});
    }
  },
};
