// Observation classification only; never an execution or physical-stop receipt.
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
