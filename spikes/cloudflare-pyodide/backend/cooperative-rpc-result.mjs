// Preserve the distinction between engine results and coroutine-only receipts.
import {parseLosslessJson} from './lossless-json.mjs';
export function cooperativeRpcResponse(wire, executionId) {
  const rpc = parseLosslessJson(wire);
  if (rpc.outcome?.kind === 'rpc_result_too_large') {
    if (rpc.sdk_close_returned !== true || typeof executionId !== 'string' || !executionId) {
      throw new Error('Completed engine cleanup and parent identity required');
    }
    return Response.json({type: 'result', execution_id: executionId, success: false,
      error_type: 'ExecutorRpcResultTooLargeError',
      error: 'Executor RPC result exceeds wire budget; workflow effects may already exist'},
    {headers: {'X-Spike-SDK-Close-Returned': 'true', 'X-Spike-RPC-Result-Too-Large': 'true'}});
  }
  if (rpc.outcome?.kind !== 'engine_result') {
    throw new Error('Cooperative coroutine settled; canonical execution remains uncertain');
  }
  return Response.json(rpc.outcome.body, {headers: {
    'X-Spike-SDK-Close-Returned': rpc.sdk_close_returned === true ? 'true' : 'false',
  }});
}
