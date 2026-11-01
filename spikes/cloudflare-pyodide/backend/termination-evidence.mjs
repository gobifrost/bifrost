// Observation classification only; never an execution or physical-stop receipt.
export function classifyTimedCancellation(events) {
  if(!Array.isArray(events) || events.some((e,i)=>!e ||
      !['before','abort_requested','caught','after'].includes(e.event) ||
      !Number.isFinite(e.elapsed_ms) || e.elapsed_ms<0 ||
      (i>0 && e.elapsed_ms<events[i-1].elapsed_ms))) {
    throw new TypeError('Malformed monotonic termination observation');
  }
  const abort=events.find(e=>e.event==='abort_requested');
  const later=abort && events.some(e=>e.event==='after' && e.elapsed_ms>abort.elapsed_ms);
  return {classification:!abort ? 'not_exercised' : later ? 'late_effect_after_abort' : 'observation_only',
    abort_request_observed:!!abort, effect_after_abort_observed:!!later,
    physical_termination_verified:false, canonical_late_result_fencing_verified:false};
}

export function classifyCancellation(body) {
  if(!body || typeof body.abort_requested!=='boolean' || !Array.isArray(body.effects) ||
      body.effects.some(value=>typeof value!=='string')) throw new TypeError('Malformed cancellation observation');
  const after=body.effects.includes('after');
  return {
    abort_request_observed:body.abort_requested,
    later_effect_observed:after,
    // Flags alone provide neither an abort timestamp nor a stop receipt.
    cancellation_prevents_later_effect:null,
    classification:!body.abort_requested ? 'not_exercised' :
      after ? 'abort_and_later_effect_observed' : 'no_later_effect_in_observation',
    effect_ordering_relative_to_abort_verified:false,
    physical_termination_verified:false,
  };
}
