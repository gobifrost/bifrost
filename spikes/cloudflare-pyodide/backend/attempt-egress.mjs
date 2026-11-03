// Companion capability lifetime only; upstream still authenticates every request.
// This cannot cancel a write already dispatched to the destination.
export function createAttemptEgress(deadline, forward, now = Date.now) {
  if (!Number.isFinite(deadline) || typeof forward !== 'function' || typeof now !== 'function') {
    throw new TypeError('Trusted finite deadline and transport required');
  }
  let closed = false;
  return {
    close() { closed = true; },
    fetch(request) {
      const time = now();
      if (closed || !Number.isFinite(time) || time >= deadline) {
        closed = true;
        return Promise.resolve(new Response('Attempt capability expired', {status: 410}));
      }
      // Do not retry, synthesize a terminal receipt, or label a transport error
      // as effect-free. The caller retains upstream reconciliation ownership.
      return forward(request);
    },
  };
}
