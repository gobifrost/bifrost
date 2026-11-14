import {freshEngineCode} from './engine-code.mjs';

export default {
  async fetch(request,env) {
    if(env.LOCAL_PROBE !== 'true') return new Response('Private diagnostic only',{status:403});
    if(request.method !== 'POST') return new Response('POST required',{status:405});
    const body=await request.json();
    if(!['json_transform','async_context','workspace_import','multi_step'].includes(body.workload)) {
      return new Response('Unsupported probe workload',{status:422});
    }
    const worker=env.LOADER.load(freshEngineCode());
    return worker.getEntrypoint().fetch(new Request('https://attempt.invalid/',{
      method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'},
    }));
  },
};
