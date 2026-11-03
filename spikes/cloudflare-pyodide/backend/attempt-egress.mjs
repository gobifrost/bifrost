// Companion capability lifetime only; upstream still authenticates every request.
// This cannot cancel a write already dispatched to the destination.
export function canonicalDeadline(context, now = Date.now()) {
  const value = context?.workflow_deadline;
  // Upstream dispatch uses datetime.now(timezone.utc).isoformat(). Require its
  // explicit UTC form; Date.parse alone accepts ambiguous and rollover dates.
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:\+00:00|Z)$/.test(value)) {
    throw new TypeError('Canonical UTC deadline required');
  }
  const deadline = Date.parse(value);
  if (!Number.isFinite(now) || !Number.isFinite(deadline) ||
      new Date(deadline).toISOString().slice(0, 19) !== value.slice(0, 19) ||
      !Number.isInteger(context.timeout_seconds) || context.timeout_seconds < 1 ||
      deadline <= now || deadline > now + context.timeout_seconds * 1000) {
    throw new RangeError('Canonical deadline outside admitted window');
  }
  return deadline;
}

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
