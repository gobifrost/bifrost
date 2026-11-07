// Synthetic destination with independently held commit. Isolated Docker network
// only, no published port; the child gateway cannot access control paths.
import {createServer} from 'node:http';
const effects = [], events = [];
let held, received = false, committed = false, released = false, requests = 0;
const server = createServer(async (request, response) => {
  if (++requests > 32) return response.writeHead(429).end();
  if (request.url === '/snapshot' && request.method === 'GET') {
    return response.end(JSON.stringify({effects, events, received, committed, released}));
  }
  if (request.url === '/release' && request.method === 'POST') {
    if (!received || released) return response.writeHead(409).end();
    released = true; events.push('parent_release');
    held(); return response.end('released');
  }
  if (effects.length >= 8 || request.method !== 'POST' || !['/write', '/redirect', '/hold'].includes(request.url)) {
    return response.writeHead(403).end();
  }
  let body = '';
  for await (const chunk of request) {body += chunk; if (body.length > 1024) return response.writeHead(413).end();}
  const effect = {path: request.url, body,
    vendor_authorization_matches: request.headers.authorization === 'Bearer synthetic-vendor'};
  if (request.url === '/hold') {
    if (received) return response.writeHead(409).end();
    received = true; events.push('hold_received');
    held = () => {
      committed = true; effects.push(effect); events.push('hold_committed');
      if (!response.destroyed) response.end('written');
    };
    return;
  }
  effects.push(effect);
  if (request.url === '/redirect') return response.writeHead(302, {location: 'https://other.example/forbidden'}).end();
  response.end('written');
});
server.listen(8820, '0.0.0.0');
setTimeout(() => {server.closeAllConnections(); server.close();}, 180000);
