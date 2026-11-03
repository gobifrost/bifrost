// Physical transport only. PostgreSQL/upstream owns business-job state.
const encoder = new TextEncoder();
const safeErrors = new Set(['Error', 'TypeError', 'RangeError', 'AbortError', 'TimeoutError', 'CpuLimitExceeded']);
export function invocationStream({runtimeHandle, executionId, execute, cleanup}) {
  let phase = 0, canceled = false, cleaned = false;
  const close = () => {if (!cleaned) {cleaned = true; cleanup();}};
  const encode = value => {
    const bytes = encoder.encode(JSON.stringify(value) + '\n');
    if (bytes.byteLength > 262144) throw new RangeError('Invocation frame too large');
    return bytes;
  };
  const identity = {protocol: 1, runtime_handle: runtimeHandle, execution_id: executionId};
  const body = new ReadableStream({
    async pull(controller) {
      if (phase === 0) {
        phase = 1;
        controller.enqueue(encode({type: 'accepted', ...identity}));
        return;
      }
      if (phase !== 1) return;
      phase = 2;
      try {
        const evidence = await execute();
        if (!canceled) controller.enqueue(encode({type: 'result', ...identity, evidence}));
      } catch (error) {
        // No raw exception text, stack, headers or workflow credentials.
        if (!canceled) controller.enqueue(encode({type: 'error', ...identity,
          error_type: safeErrors.has(error?.name) ? error.name : 'ProviderError'}));
      } finally {
        close();
        if (!canceled) controller.close();
      }
    },
    cancel() {canceled = true; close();}, // Capability cleanup, never a physical-stop claim.
  });
  return new Response(body, {headers: {'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store'}});
}
