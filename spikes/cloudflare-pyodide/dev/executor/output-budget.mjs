// Executor child-output budget: envelopes that exceed retention are dropped,
// reported as infrastructure failure, and never parsed or returned as results.
// Vocabulary mirrors the canonical path: ExecutorBodyTooLargeError with
// engine_outcome_delivered false. Effects may already exist; this must never
// trigger automatic workflow replay and never carries the oversized payload.
export const OUTPUT_BUDGET_BYTES = 1024 * 1024;

export class ExecutorBodyTooLargeError extends Error {
  constructor(limitBytes, observedBytes) {
    super('Executor response exceeds retention budget; workflow effects may already exist');
    this.name = 'ExecutorBodyTooLargeError';
    this.limitBytes = limitBytes;
    this.observedBytes = observedBytes;
  }
}

export async function readBudgetedText(response, maxBytes = OUTPUT_BUDGET_BYTES) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new TypeError('Invalid output budget');
  const reader = response.body?.getReader();
  if (!reader) return '';
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) throw new ExecutorBodyTooLargeError(maxBytes, size);
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel(error).catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder('utf-8', {fatal: true}).decode(bytes);
}

export async function readBudgetedJson(response, maxBytes = OUTPUT_BUDGET_BYTES) {
  return JSON.parse(await readBudgetedText(response, maxBytes));
}
