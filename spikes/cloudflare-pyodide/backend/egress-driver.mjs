import http from 'node:http';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFile} from 'node:fs/promises';
if (process.env.REQUEST_SIGNAL_MODE !== 'local') throw new Error('Explicit local qualification required');
const records = new Map();
const server = http.createServer(async (request, response) => {
  let text = '';
  for await (const chunk of request) {
    text += chunk;
    if (text.length > 4096) {response.writeHead(413).end(); return;}
  }
  try {
    const {id, event, status} = JSON.parse(text);
    const row = records.get(id);
    if (request.method !== 'POST' || request.url !== '/events' || !row || row.events.length >= 12 ||
        !['effect', 'before', 'after'].includes(event) || ![200, 204, 410].includes(status)) throw new Error('Invalid event');
    row.events.push({event, status, elapsed_ms: performance.now() - row.started});
    response.writeHead(204).end();
  } catch {response.writeHead(400).end();}
});
await new Promise(resolve => server.listen(8825, '127.0.0.1', resolve));
try {
  for (const mode of ['completion', 'late']) {
    const id = randomUUID(), row = {mode, events: [], started: performance.now()}; records.set(id, row);
    const response = await fetch('http://127.0.0.1:8824/', {method: 'POST', body: JSON.stringify({id, mode}), signal: AbortSignal.timeout(45000)});
    assert.equal(response.status, 200);
    row.body = await response.json();
    assert.equal(row.body.before, 204);
    assert.deepEqual(row.body.statuses, mode === 'completion' ? [204] : [410, 410, 410]);
    assert.equal(row.events.filter(item => item.event === 'effect').length, mode === 'completion' ? 2 : 1);
    if (mode === 'late') {
      assert.equal(row.body.total, 199999990000000);
      assert.equal(row.body.continued_after_rejections, true);
    }
  }
  await writeFile('/qualification/observation.json', JSON.stringify({runtime: 'local-workerd', cloud_invocations: 0,
    physical_termination_verified: false, records: [...records.values()].map(({started, ...row}) => row)}, null, 2) + '\n', {flag: 'wx'});
} finally {await new Promise(resolve => server.close(resolve));}
