// Physical transport only. PostgreSQL/upstream owns business-job state.
const encoder = new TextEncoder();
const safeErrors = new Set(['Error', 'TypeError', 'RangeError', 'AbortError', 'TimeoutError', 'CpuLimitExceeded']);
class InvocationFrameTooLarge extends Error {}
export function invocationStream({runtimeHandle, executionId, execute, cleanup}) {
  let phase = 0, canceled = false, cleaned = false;
  const close = () => {if (!cleaned) {cleaned = true; cleanup();}};
  const encode = value => {
    const bytes = encoder.encode(JSON.stringify(value) + '\n');
    if (bytes.byteLength > 262144) throw new InvocationFrameTooLarge('Invocation frame too large');
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
        if (!canceled) {
          let terminal;
          try {
            terminal = encode({type: 'result', ...identity, evidence});
          } catch (error) {
            if (!(error instanceof InvocationFrameTooLarge)) throw error;
            // Required work returned. Report delivery failure without replaying effects
            // or retaining the oversized, potentially sensitive workflow payload.
            terminal = encode({type: 'result', ...identity, evidence: {
              runtime_handle: runtimeHandle,
              result: {type: 'result', execution_id: executionId, success: false,
                error_type: 'ExecutorFrameTooLargeError',
                error: 'Executor terminal exceeds frame budget; workflow effects may already exist'},
              provider_outcome: {kind: 'terminal_frame_too_large', execute_returned: true,
                engine_outcome_delivered: false, frame_limit_bytes: 262144},
            }});
          }
          controller.enqueue(terminal);
        }
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
