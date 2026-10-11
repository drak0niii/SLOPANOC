/** Disposable loopback transport validation. No real credentials or databases. */
import assert from 'node:assert/strict';
import http from 'node:http';
import { createServer, loadConfigFromFile } from 'vite';

const listen = server => new Promise((resolve, reject) => {
  server.once('error', reject); server.listen(0, '127.0.0.1', resolve);
});
const close = server => new Promise(resolve => server.close(resolve));
const stub = name => http.createServer((request, response) => {
  response.setHeader('Content-Type', 'application/json');
  response.end(JSON.stringify({ service: name, path: request.url,
    cookie: request.headers.cookie, authorization: request.headers.authorization }));
});
const business = stub('business'), reader = stub('reader');
let vite;
try {
  await listen(business); await listen(reader);
  const loaded = await loadConfigFromFile({ command: 'serve', mode: 'development' });
  const proxy = loaded.config.server.proxy;
  const match = path => Object.keys(proxy).find(key => key.startsWith('^') ? new RegExp(key).test(path) : path.startsWith(key));
  assert.equal(proxy[match('/api/observability/access')].target, 'http://127.0.0.1:8001');
  assert.equal(proxy[match('/api/observability/sse-receipts')].target, 'http://127.0.0.1:8000');
  assert.equal(proxy[match('/api/sessions')].target, 'http://127.0.0.1:8000');
  const localProxy = Object.fromEntries(Object.entries(proxy).map(([key, value]) => [key, {
    ...value, target: `http://127.0.0.1:${value.target.endsWith(':8001') ? reader.address().port : business.address().port}`,
  }]));
  vite = await createServer({ configFile: false, ...loaded.config,
    server: { ...loaded.config.server, host: '127.0.0.1', port: 0, open: false, proxy: localProxy } });
  await vite.listen();
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}`;
  for (const path of ['/app', '/resources/observability']) {
    const response = await fetch(origin + path);
    assert.equal(response.status, 200); assert.match(await response.text(), /src\/main.tsx/);
  }
  // Synthetic credentials prove transport only, never verified identity.
  const headers = { Cookie: 'session=transport-test', Authorization: 'Bearer transport-test' };
  const access = await (await fetch(origin + '/api/observability/access', { headers })).json();
  assert.equal(access.service, 'reader'); assert.equal(access.cookie, headers.Cookie);
  assert.equal(access.authorization, headers.Authorization);
  const session = await (await fetch(origin + '/api/sessions', { headers })).json();
  assert.equal(session.service, 'business'); assert.equal(session.cookie, access.cookie);
  const receipt = await (await fetch(origin + '/api/observability/sse-receipts', { method: 'POST', headers })).json();
  assert.equal(receipt.service, 'business');
  console.log('PASS: dedicated reader routing, first-party credential forwarding, SPA paths and ANOC receipt isolation.');
} finally {
  if (vite) await vite.close();
  await Promise.all([close(reader), close(business)]);
}
