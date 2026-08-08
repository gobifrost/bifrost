import assert from "node:assert/strict";
import test from "node:test";

import {
  boundedTimeoutSeconds,
  reportLaunchFailure,
} from "./runtime_helpers.mjs";

test("boundedTimeoutSeconds applies the runner limits", () => {
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: 1 }), 30);
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: 90.9 }), 90);
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: 99_999 }), 7200);
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: "invalid" }), 300);
});

test("reportLaunchFailure sends a bounded terminal callback", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  let request;
  globalThis.fetch = async (...args) => {
    request = args;
    return new Response(null, { status: 204 });
  };

  const reported = await reportLaunchFailure(
    {
      callback_base_url: "https://bifrost.example.com/",
      job_id: "job-id",
      capability: "job-capability",
    },
    new Error("launch failed"),
  );

  assert.equal(reported, true);
  assert.equal(
    request[0],
    "https://bifrost.example.com/api/internal/sandbox/jobs/job-id/complete",
  );
  assert.equal(request[1].method, "POST");
  assert.equal(request[1].headers.Authorization, "Bearer job-capability");
  assert.ok(request[1].signal instanceof AbortSignal);
  assert.deepEqual(JSON.parse(request[1].body), {
    status: "failed",
    error: "launch failed",
  });
});

test("reportLaunchFailure never masks the original Workflow failure", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async () => {
    throw new Error("callback unavailable");
  };

  assert.equal(
    await reportLaunchFailure(
      {
        callback_base_url: "https://bifrost.example.com",
        job_id: "job-id",
        capability: "job-capability",
      },
      new Error("launch failed"),
    ),
    false,
  );
  assert.equal(await reportLaunchFailure({}, new Error("launch failed")), false);
});
