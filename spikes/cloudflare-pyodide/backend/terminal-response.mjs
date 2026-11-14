import {parseLosslessJson} from './lossless-json.mjs';

// Fixed local companion transport bounds. EOF proves response consumption,
// never physical isolate destruction or absence of detached computation.
export async function terminalResponse(response) {
  const reader = response.body?.getReader();
  if (!reader) throw new TypeError('Terminal response body required');
  const chunks = [];
  let size = 0, oversized = false;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4 * 1024 * 1024) throw new RangeError('Terminal drain budget exceeded; outcome uncertain');
      if (size > 1024 * 1024) {
        oversized = true;
        chunks.length = 0;
      }
      if (!oversized) chunks.push(value);
    }
  } catch (error) {
    await reader.cancel(error);
    throw error;
  } finally { reader.releaseLock(); }
  if (oversized) return {oversized: true, response_eof: true, response_bytes: size};
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.byteLength;}
  return {oversized: false, value: parseLosslessJson(new TextDecoder('utf-8', {fatal: true}).decode(bytes))};
}
