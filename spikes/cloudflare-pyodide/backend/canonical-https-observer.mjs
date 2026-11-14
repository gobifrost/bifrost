// Independent synthetic destination; container loopback only, bounded lifetime.
import {createServer} from 'node:http';
const effects = [];
const server = createServer(async (request, response) => {
  if (request.url === '/snapshot' && request.method === 'GET') {
    return response.end(JSON.stringify(effects));
  }
  if (effects.length >= 8 || request.method !== 'POST' || !['/write', '/redirect'].includes(request.url)) {
    return response.writeHead(403).end();
  }
  let body = '';
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 1024) return response.writeHead(413).end();
  }
  // Never record arbitrary credential headers; retain only synthetic identity.
  effects.push({path: request.url, body,
    vendor_authorization_matches: request.headers.authorization === 'Bearer synthetic-vendor'});
  if (request.url === '/redirect') return response.writeHead(302, {location: 'https://other.example/forbidden'}).end();
  response.end('written');
});
server.listen(8820, '127.0.0.1');
setTimeout(() => {server.closeAllConnections(); server.close();}, 180000);
