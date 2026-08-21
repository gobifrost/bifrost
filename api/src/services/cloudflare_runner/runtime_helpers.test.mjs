import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import test from "node:test";

import {
  boundedTimeoutSeconds,
  buildRunnerAllowedHosts,
  isRetryableWorkflowFailure,
  runnerPollCount,
  runnerProcessTerminal,
  reportLaunchFailure,
  reportTerminalWorkflowFailure,
  runnerReportedTerminalResult,
  sandboxCommandFailure,
  sandboxControlStepConfig,
  sandboxIdForPayload,
  sandboxStepConfig,
  initialRunnerAllowedHosts,
  isContainerStartingError,
  runnerSandboxIdForPayload,
  workspaceAllowedHosts,
  workspaceBrokerUrlForPayload,
  workspaceSandboxIdForPayload,
  workspaceSandboxIdForRunnerSandboxId,
} from "./runtime_helpers.mjs";

const WORKER_SOURCE = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "worker.mjs"),
  "utf8",
);

test("only Cloudflare code-update resets are retryable Workflow failures", () => {
  assert.equal(
    isRetryableWorkflowFailure(
      new Error("Durable Object reset because its code was updated."),
    ),
    true,
  );
  assert.equal(isRetryableWorkflowFailure(new Error("runner exited 1")), false);
});

test("only the exact Cloudflare container-starting error triggers a fresh probe", () => {
  const starting = new Error("Container is starting. Please retry in a moment.");
  starting.name = "SandboxError";
  assert.equal(isContainerStartingError(starting), true);
  assert.equal(
    isContainerStartingError(new Error("Container is starting. Please retry in a moment.")),
    false,
  );
  const otherSandboxError = new Error("Container exited during startup");
  otherSandboxError.name = "SandboxError";
  assert.equal(isContainerStartingError(otherSandboxError), false);
});

test("boundedTimeoutSeconds applies the runner limits", () => {
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: 1 }), 30);
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: 90.9 }), 90);
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: 99_999 }), 7200);
  assert.equal(boundedTimeoutSeconds({ timeout_seconds: "invalid" }), 300);
});

test("sandboxStepConfig retries one transient Cloudflare interruption", () => {
  assert.deepEqual(sandboxStepConfig(7200), {
    retries: { limit: 2, delay: "5 seconds", backoff: "exponential" },
    timeout: "122 minutes",
  });
});

test("sandbox process monitoring stays bounded and classifies terminal states", () => {
  assert.deepEqual(sandboxControlStepConfig(), {
    retries: { limit: 2, delay: "5 seconds", backoff: "exponential" },
    timeout: "2 minutes",
  });
  assert.equal(runnerPollCount(7200), 732);
  assert.equal(runnerProcessTerminal("running"), false);
  assert.equal(runnerProcessTerminal("completed"), true);
  assert.equal(runnerProcessTerminal("failed"), true);
});

test("sandboxIdForPayload keeps UUID probe IDs within Cloudflare's limit", () => {
  const probeId = "bifrost-probe-82eeeccc-2cf3-4720-891a-fa6bf836a14d";

  assert.equal(
    sandboxIdForPayload({ mode: "probe", probe_id: probeId }),
    probeId,
  );
  assert.ok(probeId.length <= 63);
  assert.equal(
    sandboxIdForPayload({
      job_id: "82eeeccc-2cf3-4720-891a-fa6bf836a14d",
      dispatch_attempt: 2,
    }),
    "bifrost-82eeeccc-2cf3-4720-891a-fa6bf836a14d-2-runner",
  );
});

test("builder turns derive paired runner and workspace sandbox identities", () => {
  const payload = {
    job_id: "82eeeccc-2cf3-4720-891a-fa6bf836a14d",
    dispatch_attempt: 2,
    callback_base_url: "https://debug.example.test/api/",
    runner_allowed_hosts: ["API.OpenAI.com", "api.openai.com"],
  };

  assert.equal(
    runnerSandboxIdForPayload(payload),
    "bifrost-82eeeccc-2cf3-4720-891a-fa6bf836a14d-2-runner",
  );
  assert.equal(
    workspaceSandboxIdForPayload(payload),
    "bifrost-82eeeccc-2cf3-4720-891a-fa6bf836a14d-2-workspace",
  );
  assert.equal(
    workspaceSandboxIdForRunnerSandboxId(runnerSandboxIdForPayload(payload)),
    workspaceSandboxIdForPayload(payload),
  );
  assert.equal(workspaceBrokerUrlForPayload(payload), "http://workspace.bifrost.internal");
  assert.deepEqual(initialRunnerAllowedHosts(payload), [
    "workspace.bifrost.internal",
    "debug.example.test",
    "api.openai.com",
  ]);
  assert.deepEqual(workspaceAllowedHosts(payload), []);
});

test("app builds use a locked-down callback plus package-registry egress profile", () => {
  assert.deepEqual(
    buildRunnerAllowedHosts({
      callback_base_url: "https://debug.example.test/api/internal",
    }),
    ["debug.example.test", "registry.npmjs.org"],
  );
});

test("sandboxCommandFailure returns bounded diagnostics without capabilities", () => {
  const jwt = "eyJhbGciOiJIUzI1NiJ9.eyJqb2JfaWQiOiIxMjMifQ.signature";
  const message = sandboxCommandFailure(
    {
      exitCode: 1,
      stderr: `callback failed Authorization: Bearer ${jwt}\n${"x".repeat(2500)}`,
    },
    "Runner failed",
  );

  assert.match(message, /^Runner failed \(exit 1\): /);
  assert.ok(!message.includes(jwt));
  assert.ok(message.length <= "Runner failed (exit 1): ".length + 2_000);
});

test("runner terminal exit codes do not masquerade as launch failures", () => {
  assert.equal(runnerReportedTerminalResult({ exitCode: 1 }), true);
  assert.equal(runnerReportedTerminalResult({ exitCode: 2 }), true);
  assert.equal(runnerReportedTerminalResult({ exitCode: 3 }), false);
  assert.equal(runnerReportedTerminalResult({}), false);
});

test("Worker declares the two-sandbox broker security shape", () => {
  assert.match(
    WORKER_SOURCE,
    /export class ContainerProxy extends CloudflareContainerProxy/,
  );
  assert.match(WORKER_SOURCE, /hostname === WORKSPACE_BROKER_HOST/);
  assert.match(WORKER_SOURCE, /export class Sandbox extends CloudflareSandbox/);
  assert.equal(
    (WORKER_SOURCE.match(/transport: "rpc"/g) ?? []).length,
    4,
  );
  assert.doesNotMatch(WORKER_SOURCE, /interceptHttps = true/);
  assert.match(WORKER_SOURCE, /await runner\.start\(\{ enableInternet: true \}\)/);
  assert.match(WORKER_SOURCE, /await workspace\.start\(\{ enableInternet: false \}\)/);
  assert.match(WORKER_SOURCE, /const PROBE_TIMEOUT_SECONDS = 10 \* 60/);
  assert.match(WORKER_SOURCE, /Sandbox\.outboundHandlers = \{/);
  assert.match(
    WORKER_SOURCE,
    /await runner\.setOutboundByHost\([\s\S]*"bifrostWorkspaceBroker"/,
  );
  assert.match(
    WORKER_SOURCE,
    /runnerSandboxId: runnerId, workspaceSandboxId: workspaceId/,
  );
  assert.match(WORKER_SOURCE, /prepareTurnSandboxes/);
  assert.match(WORKER_SOURCE, /await workspace\.start\(\{ enableInternet: false \}\)/);
  assert.match(WORKER_SOURCE, /await workspace\.setAllowedHosts\(workspaceAllowedHosts\(payload\)\)/);
  assert.match(WORKER_SOURCE, /destroyTurnSandboxes/);
  assert.match(WORKER_SOURCE, /crypto\.randomUUID\(\)/);
  assert.match(WORKER_SOURCE, /tool-request-\$\{requestId\}\.json/);
  assert.match(WORKER_SOURCE, /tool-response-\$\{requestId\}\.json/);
  assert.match(WORKER_SOURCE, /function boundedBodyStream/);
  assert.match(WORKER_SOURCE, /boundedBodyStream\(request, 256 \* 1024 \* 1024\)/);
  assert.match(WORKER_SOURCE, /streamFile\(stream\)\[Symbol\.asyncIterator\]\(\)/);
  assert.match(
    WORKER_SOURCE,
    /new Response\(decodedSandboxFileStream\(stream\)/,
  );
  assert.doesNotMatch(WORKER_SOURCE, /const archive = await collectBody\(request, 256/);
  assert.match(WORKER_SOURCE, /await sandbox\.start\(\{ enableInternet: true \}\)/);
  assert.match(WORKER_SOURCE, /await sandbox\.setAllowedHosts\(buildRunnerAllowedHosts\(payload\)\)/);
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

test("reportLaunchFailure marks Cloudflare rollout resets retryable", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  let body;
  globalThis.fetch = async (_url, init) => {
    body = JSON.parse(init.body);
    return new Response(null, { status: 204 });
  };

  assert.equal(
    await reportLaunchFailure(
      {
        callback_base_url: "https://bifrost.example.com",
        job_id: "job-id",
        capability: "job-capability",
      },
      new Error("Durable Object reset because its code was updated."),
    ),
    true,
  );
  assert.deepEqual(body, {
    status: "failed",
    error: "Durable Object reset because its code was updated.",
    retryable: true,
  });
});

test("reportTerminalWorkflowFailure reports only after the operation exhausts retries", async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => {
    globalThis.fetch = originalFetch;
  });
  let callbacks = 0;
  globalThis.fetch = async () => {
    callbacks += 1;
    return new Response(null, { status: 204 });
  };
  const failure = new Error("runtime update interrupted sandbox");

  await assert.rejects(
    reportTerminalWorkflowFailure(
      {
        callback_base_url: "https://bifrost.example.com",
        job_id: "job-id",
        capability: "job-capability",
      },
      async () => {
        throw failure;
      },
    ),
    failure,
  );
  assert.equal(callbacks, 1);

  await assert.rejects(
    reportTerminalWorkflowFailure({ mode: "probe" }, async () => {
      throw failure;
    }),
    failure,
  );
  assert.equal(callbacks, 1);
});
