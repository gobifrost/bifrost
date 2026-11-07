import assert from 'node:assert/strict';
import {createServer} from 'node:http';
const effects = [];
const server = createServer(async (request, response) => {
  if (effects.length >= 4 || request.method !== 'POST') return response.writeHead(403).end();
  let body = '';
  for await (const chunk of request) {body += chunk; if (body.length > 1024) return response.writeHead(413).end();}
  effects.push({path: request.url, authorization: request.headers.authorization ?? null, body});
  if (request.url === '/redirect') return response.writeHead(302, {location: 'https://other.example/write'}).end('redirect');
  response.end('written');
});
await new Promise((resolve, reject) => {server.once('error', reject); server.listen(8820, '127.0.0.1', resolve);});
try {
  const response = await fetch('http://127.0.0.1:8799/', {method: 'POST', signal: AbortSignal.timeout(45000)});
  const body = await response.json();
  console.log(JSON.stringify({status: response.status, body, effects, cloud_invocations: 0}));
  assert.equal(response.status, 200);
  assert.deepEqual(body.observed.map(item => item.status), [403, 403, 200, 302, 429]);
  assert.deepEqual(effects, [
    {path: '/write', authorization: 'Bearer vendor-token', body: 'synthetic'},
    {path: '/redirect', authorization: null, body: 'synthetic'},
  ]);
} finally {
  server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
}
