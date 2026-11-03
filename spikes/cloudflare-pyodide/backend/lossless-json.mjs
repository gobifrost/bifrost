import {boundedText} from '../probe-io.mjs';

// Opaque workflow numbers must survive Python -> JS -> Python unchanged.
// Keep ordinary safe integer control fields usable by the parent. Other numeric
// lexemes are forwarded using the engine's JSON primitives, never eval or a
// second JSON parser. No alternate parser on an unsupported runtime.
export function parseLosslessJson(text) {
  let sourceAvailable = false;
  JSON.parse('1', (_key, value, context) => {sourceAvailable = context?.source === '1'; return value;});
  if (!sourceAvailable || typeof JSON.rawJSON !== 'function') {
    throw new TypeError('Lossless JSON runtime support required');
  }
  return JSON.parse(text, (_key, value, context) => {
    if (typeof value !== 'number') return value;
    const source = context.source;
    if (/^-?\d+$/.test(source) && Number.isSafeInteger(value) && source !== '-0') return value;
    return JSON.rawJSON(source);
  });
}

export async function boundedLosslessJson(response, maxBytes) {
  return parseLosslessJson(await boundedText(response, maxBytes));
}
