import {runtimeCode} from './runtime-code.mjs';

export default {
  async fetch(request,env) {
    if(env.LOCAL_PROBE !== 'true') return new Response('Local inspection only',{status:403});
    // No credential-bearing execution endpoint until trusted control integration exists.
    if(request.method!=='POST') return new Response('POST required',{status:405});
    const body=await request.json();
    if(body.action!=='inspect') return new Response('Not admitted',{status:422});
    return env.LOADER.load(runtimeCode()).getEntrypoint().fetch(new Request('https://attempt.invalid/',{
      method:'POST',body:JSON.stringify(body),
    }));
  },
};
