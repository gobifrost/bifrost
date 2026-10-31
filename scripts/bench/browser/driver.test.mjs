import assert from "node:assert/strict";
import { once } from "node:events";
import http from "node:http";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const driver = fileURLToPath(new URL("./driver.mjs", import.meta.url));

function runDriver(config, environment) {
  const child = spawn(process.execPath, [driver, JSON.stringify(config)], {
    env: { ...process.env, ...environment },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", chunk => { stdout += chunk; });
  child.stderr.on("data", chunk => { stderr += chunk; });
  return once(child, "close").then(([code]) => ({ code, stdout, stderr }));
}

test("driver reuses three authenticated pages across all variants", async () => {
  let loginPosts = 0;
  let workflowPosts = 0;
  let concurrentRounds = 0;
  const pending = [];
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, "http://fixture.test");
    if (url.pathname === "/auth/login" && request.method === "POST") {
      loginPosts += 1;
      const headers = { "Content-Type": "application/json" };
      if (loginPosts <= 3) headers["Set-Cookie"] = "session=fixture; HttpOnly; Path=/";
      response.writeHead(loginPosts <= 3 ? 200 : 429, headers);
      response.end(JSON.stringify({ ok: loginPosts <= 3 }));
      return;
    }
    if (url.pathname === "/login") {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end(`<!doctype html><label>Email<input aria-label="Email"></label><label>Password<input aria-label="Password" type="password"></label><button>Sign In</button><script>
        document.querySelector('button').onclick = async () => {
          await fetch('/auth/login', { method: 'POST', body: new FormData() });
          location.assign('/home');
        };
      </script>`);
      return;
    }
    if (url.pathname.startsWith("/apps/")) {
      if (!request.headers.cookie?.includes("session=fixture")) {
        response.writeHead(401); response.end("missing cookie"); return;
      }
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end(`<!doctype html><button aria-label="Run Browser Benchmark">Run Browser Benchmark</button><script>
        let ordinal = 0;
        document.querySelector('button').onclick = async () => {
          const events = window.__BENCH_BROWSER_EVENTS__ || (window.__BENCH_BROWSER_EVENTS__ = []);
          events.push({ type: 'invoke', request_id: 'request-' + (++ordinal), t_ms: performance.now() });
          await fetch('/api/workflows/execute', { method: 'POST' });
          requestAnimationFrame(() => events.push({ type: 'render', status: 'Success', t_ms: performance.now() }));
        };
      </script>`);
      return;
    }
    if (url.pathname === "/api/workflows/execute") {
      pending.push({ response, id: ++workflowPosts });
      // A serial action driver cannot complete this fixture: each round needs
      // all three authenticated pages to submit before any response is sent.
      if (pending.length === 3) {
        concurrentRounds += 1;
        for (const item of pending.splice(0)) {
          item.response.writeHead(200, { "Content-Type": "application/json" });
          item.response.end(JSON.stringify({ execution_id: `execution-${item.id}`, status: "Success" }));
        }
      }
      return;
    }
    response.writeHead(200, { "Content-Type": "text/html" });
    response.end("<!doctype html><p>home</p>");
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const { port } = server.address();
  try {
    const result = await runDriver({
      client_url: `http://127.0.0.1:${port}`,
      app_slug: "fixture",
      batches: ["control", "same_body_graph", "shallow_list", "detail_hydration"].map(variant =>
        [0, 1, 2].map(ordinal => ({ variant, ordinal }))),
    }, { BIFROST_DEFAULT_USER_EMAIL: "fixture@example.test", BIFROST_DEFAULT_USER_PASSWORD: "fixture-password" });
    assert.equal(result.code, 0, result.stderr);
    const output = JSON.parse(result.stdout);
    assert.equal(loginPosts, 3);
    assert.equal(output.logins.length, 3);
    assert.equal(output.context_count, 3);
    assert.equal(output.runs.length, 24);
    assert.equal(workflowPosts, 24);
    assert.equal(concurrentRounds, 8);
    assert.ok(output.runs.every(run => run.outcome === "success"));
  } finally {
    server.close();
    await once(server, "close");
  }
});
