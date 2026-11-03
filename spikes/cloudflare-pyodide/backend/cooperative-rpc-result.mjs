// Preserve the distinction between engine results and coroutine-only receipts.
import {parseLosslessJson} from './lossless-json.mjs';
export function cooperativeRpcResponse(wire) {
  const rpc = parseLosslessJson(wire);
  if (rpc.outcome?.kind !== 'engine_result') {
    throw new Error('Cooperative coroutine settled; canonical execution remains uncertain');
  }
  return Response.json(rpc.outcome.body, {headers: {
    'X-Spike-SDK-Close-Returned': rpc.sdk_close_returned === true ? 'true' : 'false',
  }});
}
