// Metadata allowlist only. A tail receipt is not automatically physical-stop proof.
const outcomes = ['unknown', 'ok', 'exception', 'exceededCpu', 'exceededMemory', 'scriptNotFound', 'canceled', 'responseStreamDisconnected'];
const metric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
export function summarizeTail(events) {
  if (!Array.isArray(events) || events.length !== 1 || !events[0] ||
      (events[0].outcome !== undefined && !outcomes.includes(events[0].outcome))) throw new TypeError('Unsupported tail envelope');
  const event = events[0];
  return {schema: 1, outcome: event.outcome ?? null, event_timestamp: metric(event.eventTimestamp),
    log_count: Array.isArray(event.logs) ? event.logs.length : null,
    exception_count: Array.isArray(event.exceptions) ? event.exceptions.length : null,
    // Retain field names/values without inferring undocumented units.
    raw_numeric_fields: {cpuTime: metric(event.cpuTime), cpuTimeMs: metric(event.cpuTimeMs),
      wallTime: metric(event.wallTime), wallTimeMs: metric(event.wallTimeMs)}};
}
