import assert from "node:assert/strict";
import test from "node:test";

import { browserObserverScript } from "./observer.mjs";

test("the injected observer preserves the URL and FormData supplied to login", async () => {
  const form = new FormData();
  form.append("username", "benchmark@example.test");
  form.append("password", "not-a-real-password");
  const loginInit = {
    method: "POST",
    body: form,
  };
  let received;
  const originalWindow = globalThis.window;
  globalThis.window = {
    fetch: async (...args) => {
      received = args;
      return new Response("{}", { headers: { "Content-Type": "application/json" } });
    },
  };
  try {
    eval(browserObserverScript());
    await globalThis.window.fetch("/auth/login", loginInit);
    assert.strictEqual(received[0], "/auth/login");
    assert.strictEqual(received[1], loginInit);
    assert.equal(form.get("username"), "benchmark@example.test");
  } finally {
    globalThis.window = originalWindow;
  }
});

test("an unparseable observed workflow response still reaches the application", async () => {
  const originalWindow = globalThis.window;
  globalThis.window = {
    fetch: async () => new Response("not-json", { status: 200 }),
    location: { origin: "https://client.bench-tenant" },
  };
  try {
    eval(browserObserverScript());
    const response = await globalThis.window.fetch("/api/workflows/execute", { method: "POST" });
    assert.equal(await response.text(), "not-json");
  } finally {
    globalThis.window = originalWindow;
  }
});

test("the observer honors RequestInit method precedence and document-relative URLs", async () => {
  const originalWindow = globalThis.window;
  globalThis.window = {
    __BENCH_BROWSER_EVENTS__: [],
    document: { baseURI: "https://client.bench-tenant/apps/fixture/" },
    fetch: async () => Response.json({ execution_id: "execution-1", status: "Success" }),
  };
  try {
    eval(browserObserverScript());
    const request = new Request("https://client.bench-tenant/api/workflows/execute", { method: "GET" });
    await globalThis.window.fetch(request, { method: "POST" });
    await globalThis.window.fetch("api/executions/execution-2");
    assert.deepEqual(globalThis.window.__BENCH_BROWSER_EVENTS__.map(({ type, method, path }) => ({ type, method, path })), [
      { type: "post", method: "POST", path: "/api/workflows/execute" },
    ]);
  } finally {
    globalThis.window = originalWindow;
  }
});
