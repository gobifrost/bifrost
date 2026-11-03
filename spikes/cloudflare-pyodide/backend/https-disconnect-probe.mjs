import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {writeFileSync} from 'node:fs';
const states = new Map(); let requests = 0;
const server = createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1'), state = states.get(url.searchParams.get('id'));
  if (++requests > 8 || !state || request.method !== 'POST') return response.writeHead(403).end();
  if (url.pathname === '/hold') {
    state.received = true; state.events.push('hold_received');
    state.release = () => {
      state.events.push('parent_release'); state.committed = true; state.events.push('hold_committed');
      if (!response.destroyed) response.end('written');
    };
    response.on('close', () => {if (!state.committed) state.events.push('held_connection_closed');});
    return;
  }
  if (!['/before', '/after'].includes(url.pathname)) return response.writeHead(403).end();
  state.effects.push(url.pathname); response.end('written');
});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check) {
  const cutoff = Date.now() + 15000;
  while (!await check()) {if (Date.now() >= cutoff) throw new Error('Bounded causal fixture condition failed'); await pause(25);}
}
await new Promise((resolve, reject) => {server.once('error', reject); server.listen(8820, '127.0.0.1', resolve);});
const observations = {};
let active, failure;
try {
  for (const mode of ['control', 'disconnect']) {
    const id = crypto.randomUUID(), state = {received: false, committed: false, events: [], effects: []};
    states.set(id, state);
    const response = await fetch(`http://127.0.0.1:8799/?id=${id}`, {method: 'POST', signal: AbortSignal.timeout(25000)});
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    const first = await reader.read();
    const frame = JSON.parse(new TextDecoder().decode(first.value).trim());
    assert.equal(frame.type, 'accepted'); assert.equal(frame.execution_id, id);
    // Demand further bytes to start execute(), while retaining a pending read.
    const next = reader.read();
    await until(() => state.received);
    const status = async () => (await (await fetch(`http://127.0.0.1:8799/status?id=${id}`)).json());
    const before = {destination_committed: state.committed, parent: await status()};
    active = {id, mode, status, phase: 'held', before};
    if (mode === 'disconnect') {
      await reader.cancel();
      active.phase = 'awaiting_gate_close';
      await until(async () => (await status()).closed);
      assert.equal(state.committed, false);
    }
    const atRelease = {destination_committed: state.committed, parent: await status()};
    state.release();
    let terminal = null, eof = false;
    if (mode === 'control') {
      const chunks = [await next];
      while (!chunks.at(-1).done) chunks.push(await reader.read());
      const text = chunks.filter(chunk => !chunk.done).map(chunk => new TextDecoder().decode(chunk.value)).join('');
      terminal = JSON.parse(text.trim()); eof = true;
      assert.equal(terminal.type, 'result');
      assert.deepEqual(terminal.evidence.result.result.statuses, [200, 200, 200]);
    } else {await next;}
    await pause(500);
    observations[mode] = {before, at_release: atRelease, after: await status(),
      events: state.events, effects: state.effects, destination_committed: state.committed,
      terminal, eof_observed: eof, original_collection_uncertain: mode === 'disconnect'};
    assert.deepEqual(state.effects, mode === 'control' ? ['/before', '/after'] : ['/before']);
    assert.equal(state.committed, true);
  }
} catch (error) {
  failure = error;
} finally {
  let atFailure = null;
  if (failure && active) {
    const state = states.get(active.id);
    atFailure = {mode: active.mode, phase: active.phase, parent: await active.status(),
      received: state.received, committed: state.committed, effects: [...state.effects], events: [...state.events]};
  }
  for (const state of states.values()) if (state.release && !state.committed) state.release();
  await pause(500);
  let afterFailureRelease = null;
  if (failure && active) {
    const state = states.get(active.id);
    afterFailureRelease = {parent: await active.status(), committed: state.committed,
      effects: [...state.effects], events: [...state.events]};
  }
  const report = {status: failure ? 500 : 200, body: {observations, at_failure: atFailure,
    after_failure_release: afterFailureRelease, failure_type: failure?.name ?? null,
    observation_window_ms: 500, cloud_invocations: 0, physical_termination_verified: false, canonical_recovery_verified: false}};
  writeFileSync('/qualification/disconnect-observation.json', JSON.stringify(report, null, 2) + '\n', {flag: 'wx'});
  console.log(JSON.stringify(report));
  server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
}
if (failure) throw failure;
