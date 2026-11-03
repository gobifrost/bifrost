import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {writeFileSync} from 'node:fs';
const effects = [];
const server = createServer(async (request, response) => {
  if (effects.length >= 8) return response.writeHead(429).end();
  let body = '';
  for await (const chunk of request) {body += chunk; if (body.length > 1024) return response.writeHead(413).end();}
  effects.push({path: request.url, method: request.method, authorization: request.headers.authorization ?? null, body});
  if (request.url === '/redirect') return response.writeHead(302, {location: 'https://other.example/forbidden'}).end();
  if (request.url === '/same-origin') return response.writeHead(307, {location: 'https://vendor.example/final'}).end();
  response.end('written');
});
await new Promise((resolve, reject) => {server.once('error', reject); server.listen(8820, '127.0.0.1', resolve);});
try {
  const response = await fetch('http://127.0.0.1:8799/', {method: 'POST', signal: AbortSignal.timeout(45000)});
  const body = await response.json();
  const observation = {status: response.status, body, effects, cloud_invocations: 0};
  // Fixed owned run directory, exclusive creation; retain evidence even if an
  // assertion fails after Python completes. No workflow-selected file path.
  writeFileSync('/qualification/httpx-observation.json', JSON.stringify(observation, null, 2) + '\n', {flag: 'wx'});
  console.log(JSON.stringify(observation));
  assert.equal(response.status, 200);
  assert.deepEqual(body.rows.map(row => row.status), [200, 403, 200]);
  assert.deepEqual(body.rows.map(row => row.history), [[], [302], [307]]);
  assert.deepEqual(effects.map(effect => effect.path), ['/write', '/redirect', '/same-origin', '/final']);
  assert.ok(effects.every(effect => effect.authorization === 'Bearer vendor-token' && effect.body === 'synthetic'));
} finally {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));}
