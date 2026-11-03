import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {writeFileSync} from 'node:fs';

const states = new Map(); let requests = 0;
function snapshot(state) {
  return {received: state.received, committed: state.committed, effects: [...state.effects], events: [...state.events]};
}
const server = createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  const id = url.searchParams.get('id');
  if (++requests > 64 || request.method !== 'POST' || !/^[a-f0-9-]{36}$/.test(id ?? '')) {
    return response.writeHead(403).end();
  }
  if (url.pathname === '/new' && states.size < 3 && !states.has(id)) {
    states.set(id, {received: false, committed: false, effects: [], events: [], waiters: []});
    return response.end('{}');
  }
  const state = states.get(id);
  if (!state) return response.writeHead(403).end();
  if (url.pathname === '/hold' && !state.received) {
    state.received = true; state.events.push('hold_received');
    state.release = () => {
      if (state.committed) return;
      state.committed = true; state.events.push('accepted_write_committed');
      if (!response.destroyed) response.end('written');
    };
    response.once('close', () => {if (!state.committed) state.events.push('connection_closed_before_commit');});
    for (const waiter of state.waiters) waiter.end(JSON.stringify(snapshot(state)));
    state.waiters.length = 0;
    return;
  }
  if (url.pathname === '/wait' && !state.received) {state.waiters.push(response); return;}
  if (url.pathname === '/after') {state.effects.push('/after'); return response.end('written');}
  if (url.pathname === '/release') state.release?.();
  if (['/wait', '/release', '/snapshot'].includes(url.pathname)) return response.end(JSON.stringify(snapshot(state)));
  response.writeHead(403).end();
});
await new Promise((resolve, reject) => {server.once('error', reject); server.listen(8820, '127.0.0.1', resolve);});
let report, failure;
try {
  const response = await fetch('http://127.0.0.1:8799/', {method: 'POST',
    headers: {'x-cooperative-io': 'true', 'x-owner-scheduled': 'true'}, signal: AbortSignal.timeout(45000)});
  const text = await response.text();
  report = {status: response.status, text, cloud_invocations: 0};
  assert.equal(response.status, 200);
  const body = JSON.parse(text);
  assert.deepEqual(body.cases.map(item => item.mode), ['normal', 'cancel', 'suppress']);
  for (const item of body.cases) {
    assert.equal(item.entered, true); assert.equal(item.wrongRun, false); assert.equal(item.lateCancel, false);
    assert.equal(item.body.active_slots, 0); assert.deepEqual(item.body.cleaned, [true]);
    assert.equal(item.held.received, true); assert.equal(item.held.committed, false);
    assert.equal(item.after_release.committed, true); assert.deepEqual(item.after_release.effects, []);
    if (item.mode !== 'normal') {
      assert.equal(item.requested, true);
      assert.equal(item.before_release.committed, false);
    }
  }
  assert.deepEqual(body.cases[0].body.outcome, {status: 'Success', result: 'normal', http_status: 200});
  assert.deepEqual(body.cases[1].body.outcome, {cooperative_task_cancelled: true, physical_stopped: false, execution_settled: false});
  assert.deepEqual(body.cases[2].body.outcome, {status: 'Success', result: 'suppressed', after_status: 410});
  report.body = body;
} catch (error) {failure = error;} finally {
  const beforeCleanup = Object.fromEntries([...states].map(([id, state]) => [id, snapshot(state)]));
  for (const state of states.values()) state.release?.();
  report = {...report, before_cleanup: beforeCleanup, failure: failure?.message ?? null,
    physical_termination_verified: false, engine_sdk_execution_verified: false};
  writeFileSync('/qualification/cooperative-http-observation.json', JSON.stringify(report, null, 2), {flag: 'wx'});
  server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
}
if (failure) throw failure;
console.log(JSON.stringify({status: report.status, body: report.body, cloud_invocations: 0}));
