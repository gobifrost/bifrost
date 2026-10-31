import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { Miniflare } from "miniflare";
import worker from "../dist/index.js";

const installationId = "A0B1C2D3-E4F5-4A67-8B9C-0D1E2F3A4B5C";

function completedDay(daysAgo = 1) {
  const today = new Date();
  return new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()) - daysAgo * 86_400_000)
    .toISOString()
    .slice(0, 10);
}

function report(overrides = {}) {
  return {
    schema_version: 1,
    installation_id: installationId,
    version: "1.2.3-rc.1+build.7",
    period: completedDay(),
    workflow_runs: 4,
    agent_runs: 2,
    environment: "production",
    ...overrides,
  };
}

async function createWorker() {
  const scriptPath = new URL("../dist/index.js", import.meta.url);
  const mf = new Miniflare({
    workers: [{
      config: {
        name: "usage-reporting",
        compatibilityDate: "2026-10-30",
        manifest: {
          mainModule: "index.js",
          modulesRoot: new URL("../dist/", import.meta.url).pathname,
          modules: {
            "index.js": { type: "esm", contents: await readFile(scriptPath, "utf8") },
          },
        },
        env: { DB: { type: "d1" } },
      },
    }],
  });
  try {
    const db = await mf.getD1Database("DB", "usage-reporting");
    const migration = await readFile(new URL("../migrations/0001_usage_reports.sql", import.meta.url), "utf8");
    for (const statement of migration.split(";").map((part) => part.trim()).filter(Boolean)) {
      await db.prepare(statement).run();
    }
    return { mf, db };
  } catch (error) {
    await mf.dispose();
    throw error;
  }
}

async function request(mf, path, options = {}) {
  return mf.dispatchFetch(`https://reports.gobifrost.com${path}`, options);
}

test("health is static and does not write to D1", async (t) => {
  const { mf, db } = await createWorker();
  t.after(() => mf.dispose());

  const response = await request(mf, "/health");

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "ok");
  assert.equal((await db.prepare("SELECT COUNT(*) AS count FROM usage_reports").first()).count, 0);
});

test("stores a normalized snapshot and replaces duplicate reports", async (t) => {
  const { mf, db } = await createWorker();
  t.after(() => mf.dispose());

  assert.equal((await request(mf, "/usage", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "cf-connecting-ip": "203.0.113.19",
      "x-forwarded-for": "203.0.113.20",
      "user-agent": "private-request-metadata",
      "authorization": "Bearer unpersisted-test-value",
    },
    body: JSON.stringify(report()),
  })).status, 204);
  assert.equal((await request(mf, "/usage", {
    method: "POST",
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify(report({ workflow_runs: 9, agent_runs: 5 })),
  })).status, 204);

  const rows = await db.prepare("SELECT * FROM usage_reports").all();
  assert.equal(rows.results.length, 1);
  assert.deepEqual(rows.results[0], {
    installation_id: installationId.toLowerCase(),
    period: completedDay(),
    received_at: rows.results[0].received_at,
    schema_version: 1,
    version: "1.2.3-rc.1+build.7",
    workflow_runs: 9,
    agent_runs: 5,
  });
  assert.match(rows.results[0].received_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
});

test("accepts zero counts and the oldest permitted completed UTC day", async (t) => {
  const { mf, db } = await createWorker();
  t.after(() => mf.dispose());

  const response = await request(mf, "/usage", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(report({ period: completedDay(30), workflow_runs: 0, agent_runs: 0 })),
  });

  assert.equal(response.status, 204);
  assert.deepEqual((await db.prepare("SELECT period, workflow_runs, agent_runs FROM usage_reports").first()), {
    period: completedDay(30),
    workflow_runs: 0,
    agent_runs: 0,
  });
});

test("rejects invalid, incomplete, and non-production reports without storing input", async (t) => {
  const { mf, db } = await createWorker();
  t.after(() => mf.dispose());
  const cases = [
    report({ environment: "staging" }),
    report({ environment: "development" }),
    report({ environment: "testing" }),
    report({ extra: "unpersisted freeform value" }),
    { ...report(), agent_runs: undefined },
    null,
    [],
    report({ installation_id: "not-a-uuid" }),
    report({ version: "v1.2.3" }),
    report({ version: "1.2.3-01" }),
    report({ workflow_runs: -1 }),
    report({ workflow_runs: 0.5 }),
    report({ workflow_runs: true }),
    report({ agent_runs: null }),
    report({ agent_runs: 1_000_000_001 }),
    report({ period: completedDay(0) }),
    report({ period: completedDay(-1) }),
    report({ period: completedDay(31) }),
  ];

  for (const payload of cases) {
    const response = await request(mf, "/usage", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    assert.equal(response.status, 400);
  }

  assert.equal((await db.prepare("SELECT COUNT(*) AS count FROM usage_reports").first()).count, 0);
});

test("rejects invalid request controls and enforces the streamed body limit", async (t) => {
  const { mf, db } = await createWorker();
  t.after(() => mf.dispose());
  const cases = [
    ["/usage?unexpected=true", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(report()) }, 400],
    ["/usage", { method: "POST", headers: { "content-type": "text/plain" }, body: JSON.stringify(report()) }, 415],
    ["/usage", { method: "POST", headers: { "content-type": "application/json", "content-encoding": "gzip" }, body: JSON.stringify(report()) }, 415],
    ["/usage", { method: "POST", headers: { "content-type": "application/json" }, body: "{not json}" }, 400],
    ["/usage", { method: "POST", headers: { "content-type": "application/json" }, body: new Uint8Array([0xc3, 0x28]) }, 400],
    ["/usage", { method: "POST", headers: { "content-type": "application/json" }, body: new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(2048));
        controller.enqueue(new Uint8Array(2049));
        controller.close();
      },
    }), duplex: "half" }, 413],
  ];

  for (const [path, options, status] of cases) {
    const response = await request(mf, path, options);
    assert.equal(response.status, status);
  }
  assert.equal((await db.prepare("SELECT COUNT(*) AS count FROM usage_reports").first()).count, 0);
});

test("exposes only the health and usage routes", async (t) => {
  const { mf, db } = await createWorker();
  t.after(() => mf.dispose());

  assert.equal((await request(mf, "/usage", { method: "GET" })).status, 405);
  assert.equal((await request(mf, "/health", { method: "POST" })).status, 405);
  assert.equal((await request(mf, "/admin", { method: "GET" })).status, 404);
  assert.equal((await db.prepare("SELECT COUNT(*) AS count FROM usage_reports").first()).count, 0);
});

test("returns a generic 503 without logging or echoing a valid report when D1 fails", async (t) => {
  const logs = [];
  for (const method of ["debug", "info", "log", "warn", "error"]) {
    t.mock.method(console, method, (...args) => logs.push(args));
  }
  const payload = report({ version: "1.2.3+private-build-metadata" });
  const response = await worker.fetch(new Request("https://reports.gobifrost.com/usage", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  }), {
    DB: {
      prepare() {
        return {
          bind() {
            return { run: async () => { throw new Error("D1 unavailable"); } };
          },
        };
      },
    },
  });

  assert.equal(response.status, 503);
  assert.equal(await response.text(), "Service unavailable");
  assert.equal(logs.length, 0);
});
