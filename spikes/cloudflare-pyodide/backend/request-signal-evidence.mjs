// Offline observation interpretation only, never physical-stop admission.
export function classifySignalRecord(record) {
  if (!record || !['control', 'io_abort', 'cpu_abort'].includes(record.mode) || !Array.isArray(record.events) ||
      record.events.some((event, index) => !['before', 'caught', 'after', 'abort_requested', 'parent_abort_received'].includes(event.event) ||
        !Number.isFinite(event.elapsed_ms) || event.elapsed_ms < 0 ||
        (index > 0 && event.elapsed_ms < record.events[index - 1].elapsed_ms))) throw new TypeError('Invalid signal observation');
  const abort = record.events.find(event => event.event === 'abort_requested');
  const parent = record.events.find(event => event.event === 'parent_abort_received');
  const after = record.events.find(event => event.event === 'after');
  const classification = record.mode === 'control' ? (after ? 'completion_control_passed' : 'completion_control_failed')
    : !abort ? 'not_exercised' : !parent ? 'incoming_abort_not_observed'
      : after ? (after.elapsed_ms > parent.elapsed_ms ? 'effect_after_parent_abort' : 'late_parent_abort_observation')
        : 'physical_stop_unproven';
  return {classification, incoming_abort_observed: !!parent, physical_termination_verified: false};
}
