// Compatibility only: authenticated fixture guard and upstream identity remain required.
import {admitFixture} from '../../backend/fixture-admission.mjs';
const supported = new Set(['json', 'async-workflow', 'pinned-workspace', 'bifrost-http-sdk']);
export async function admitCanonicalFixture(body, active, execute) {
  // Legacy fixed fixtures have no declaration. Any supplied declaration must
  // fit the SDK-only candidate; omission never grants arbitrary-code admission.
  const requirements = body?.requirements;
  if (requirements !== undefined && (!Array.isArray(requirements) || requirements.length > 32 ||
      requirements.some(value => typeof value !== 'string' || !supported.has(value)) ||
      new Set(requirements).size !== requirements.length)) {
    return Response.json({error: 'Fixture not admitted', reason: 'unsupported_requirements'}, {status: 422});
  }
  return admitFixture(body, active, execute);
}
