import {createServer} from 'node:http';
const effects = [], events = [];
let received = false, committed = false, release, requests = 0;
const server = createServer((request, response) => {
  const path = new URL(request.url, 'http://fixture.invalid').pathname;
  if (++requests > 128) return response.writeHead(429).end();
  if (path === '/snapshot' && request.method === 'GET') return response.end(JSON.stringify({effects, events, received, committed}));
  if (path === '/release' && request.method === 'POST') {
    if (!release || committed) return response.writeHead(409).end();
    release(); return response.end('released');
  }
  if (request.method !== 'POST') return response.writeHead(403).end();
  if (path === '/hold') {
    if (received) return response.writeHead(409).end();
    received = true; events.push('hold_received');
    release = () => {events.push('parent_release'); effects.push('/hold'); committed = true; response.end('written');};
    return;
  }
  if (!['/before', '/after'].includes(path) || effects.length > 4) return response.writeHead(403).end();
  effects.push(path); response.end('written');
});
// Only the isolated project network, never a published port.
server.listen(8820, '0.0.0.0');
setTimeout(() => {server.closeAllConnections(); server.close();}, 120000);
