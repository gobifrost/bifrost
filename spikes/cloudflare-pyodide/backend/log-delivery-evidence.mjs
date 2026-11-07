// Transport observations only: no log replay, identity inference or persistence claim.
export function logDeliveryEvidence(logs, calls) {
  const unsuccessful = calls.filter(call => call.method === 'POST'
    && call.path === '/api/sdk/runtime/logs' && Number.isInteger(call.status)
    && (call.status < 200 || call.status >= 300));
  if (unsuccessful.length === 0) return null;
  return {
    kind: 'runtime_log_delivery_unconfirmed',
    unsuccessful_log_requests: unsuccessful.length,
    captured_log_entries: Array.isArray(logs) ? logs.length : null,
    persistence_verified: false,
    automatic_replay_safe: false,
  };
}
