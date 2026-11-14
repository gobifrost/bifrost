import assert from 'node:assert/strict';
import test from 'node:test';
import {cooperativeRpcResponse} from './cooperative-rpc-result.mjs';
import {terminalResponse} from './terminal-response.mjs';

test('RPC result roundtrip retains engine numeric values through HTTP framing', async () => {
  const body = '{"type":"result","execution_id":"run","success":true,"result":{"integer":9007199254740993,"negative":-9007199254740993,"fraction":0.1,"zero":-0.0,"exponent":1e20}}';
  const wire = `{"outcome":{"kind":"engine_result","body":${body}},"sdk_close_returned":true}`;
  const response = cooperativeRpcResponse(wire);
  assert.equal(response.headers.get('X-Spike-SDK-Close-Returned'), 'true');
  const terminal = await terminalResponse(response);
  assert.equal(JSON.stringify(terminal.value), body);
});

test('coroutine-only receipt never becomes a canonical result or SDK-close proof', () => {
  assert.throws(() => cooperativeRpcResponse(JSON.stringify({outcome: {
    cooperative_task_cancelled: true, physical_stopped: false, execution_settled: false,
  }, sdk_close_returned: true})), /canonical execution remains uncertain/);
});

test('completed engine RPC oversize maps to explicit provider failure bound to parent identity', async () => {
  const wire = JSON.stringify({outcome: {kind: 'rpc_result_too_large'}, sdk_close_returned: true});
  const response = cooperativeRpcResponse(wire, 'parent-run');
  const result = (await terminalResponse(response)).value;
  assert.equal(result.execution_id, 'parent-run');
  assert.equal(result.success, false);
  assert.equal(result.error_type, 'ExecutorRpcResultTooLargeError');
  assert.equal(response.headers.get('X-Spike-RPC-Result-Too-Large'), 'true');
  assert.throws(() => cooperativeRpcResponse(wire), /parent identity required/);
  assert.throws(() => cooperativeRpcResponse(JSON.stringify({outcome: {kind: 'rpc_result_too_large'},
    sdk_close_returned: false}), 'parent-run'), /cleanup and parent identity required/);
});
