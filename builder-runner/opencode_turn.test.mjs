import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  completionFromResponse,
  describeStructuredError,
  executeTurn,
  isRecoverableTransportFailure,
  longRunningTransportTimeoutMs,
  promptWithActivityWatchdog,
  promptWithDiagnostics,
  promptWithTransportRecovery,
  summarizeSessionMessages,
} from "./opencode_turn.mjs";

test("executeTurn persists its session id before prompting", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bifrost-session-marker-"));
  const marker = path.join(root, "session.json");
  const client = {
    session: {
      create: async () => ({ data: { id: "ses_marker" } }),
      messages: async () => ({ data: [] }),
      prompt: async () => ({
        data: {
          info: { modelID: "test-model", tokens: {} },
          parts: [{ type: "text", text: "Done" }],
        },
      }),
    },
  };

  try {
    const result = await executeTurn(
      {
        config: {},
        directory: root,
        prompt: "Build it",
        model: "test-model",
        title: "Marker test",
        sessionMarkerPath: marker,
        timeoutSeconds: 60,
      },
      {
        createRuntime: async () => ({ client, close: async () => {} }),
      },
    );

    assert.equal(result.harness_session_id, "ses_marker");
    assert.deepEqual(JSON.parse(await readFile(marker, "utf8")), {
      schema_version: 1,
      session_id: "ses_marker",
    });
    assert.equal((await stat(marker)).mode & 0o777, 0o600);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("long-running SDK transport outlives the bounded Builder turn", () => {
  assert.equal(longRunningTransportTimeoutMs(1), 61_000);
  assert.equal(longRunningTransportTimeoutMs(7_200), 7_260_000);
  assert.throws(() => longRunningTransportTimeoutMs(0), /between 1 and 7200/);
  assert.throws(() => longRunningTransportTimeoutMs(7_201), /between 1 and 7200/);
});

test("completionFromResponse returns bounded text, usage, tools, and session", () => {
  const completion = completionFromResponse(
    {
      info: {
        modelID: "deepseek-v4",
        tokens: { input: 120, output: 35 },
      },
      parts: [
        { type: "tool", tool: "write" },
        { type: "text", text: "  Built the app.  " },
        { type: "text", text: "Ignored", ignored: true },
      ],
    },
    "ses_123",
    "fallback",
  );

  assert.deepEqual(completion, {
    status: "succeeded",
    final_text: "Built the app.",
    tool_call_count: 1,
    model: "deepseek-v4",
    token_count_input: 120,
    token_count_output: 35,
    harness_session_id: "ses_123",
  });
});

test("completionFromResponse rejects model and empty-response failures", () => {
  assert.throws(
    () =>
      completionFromResponse(
        {
          info: { error: { name: "APIError", data: { message: "rate limited" } } },
          parts: [],
        },
        "ses_123",
        "fallback",
      ),
    /rate limited/,
  );
  assert.throws(
    () => completionFromResponse({ info: {}, parts: [] }, "ses_123", "fallback"),
    /without a user-facing response/,
  );
});

test("summarizeSessionMessages reports bounded aggregates without content", () => {
  const diagnostics = summarizeSessionMessages(
    [
      { info: { id: "old", role: "assistant" }, parts: [] },
      {
        info: { id: "new-1", role: "assistant" },
        parts: [
          { type: "tool", tool: "write", state: { status: "completed" } },
          { type: "tool", tool: "write", state: { status: "error" } },
          { type: "compaction" },
          { type: "text", text: "private model output" },
        ],
      },
      {
        info: { id: "new-2", role: "assistant" },
        parts: [
          { type: "tool", tool: "read", state: { status: "completed" } },
          { type: "retry" },
        ],
      },
    ],
    new Set(["old"]),
  );

  assert.deepEqual(diagnostics, {
    message_count: 2,
    assistant_message_count: 2,
    tool_call_count: 3,
    tool_error_count: 1,
    other_tool_call_count: 0,
    compaction_count: 1,
    retry_count: 1,
    truncated: false,
    tools: [
      { name: "write", count: 2, error_count: 1 },
      { name: "read", count: 1, error_count: 0 },
    ],
  });
  assert.equal(JSON.stringify(diagnostics).includes("private model output"), false);
});

test("activity watchdog aborts a session whose tool makes no progress", async () => {
  let aborted = 0;
  const old = Date.now() - 60_000;
  const client = {
    session: {
      prompt: async () => new Promise(() => {}),
      messages: async () => ({
        data: [
          {
            info: { id: "assistant", role: "assistant", time: { created: old } },
            parts: [
              {
                type: "tool",
                tool: "bash",
                state: { status: "running" },
                time: { start: old },
              },
            ],
          },
        ],
      }),
      abort: async () => {
        aborted += 1;
        return { data: true };
      },
    },
  };

  await assert.rejects(
    promptWithActivityWatchdog(client, "ses_123", "/workspace", {}, {
      timeoutMs: 10,
      pollIntervalMs: 1,
    }),
    /no observable model or tool progress/,
  );
  assert.equal(aborted, 1);
});

test("describeStructuredError preserves provider response diagnostics", () => {
  assert.equal(
    describeStructuredError({
      name: "APIError",
      data: {
        message: "Provider request failed",
        responseBody: "invalid tool result",
      },
    }),
    "Provider request failed: invalid tool result: APIError",
  );
});

test("describeStructuredError preserves nested transport codes", () => {
  const cause = new Error("other side closed");
  cause.code = "UND_ERR_SOCKET";
  assert.equal(
    describeStructuredError(new TypeError("fetch failed", { cause })),
    "fetch failed: UND_ERR_SOCKET: other side closed",
  );
});

test("promptWithDiagnostics recovers the persisted assistant failure", async () => {
  const client = {
    session: {
      prompt: async () => {
        throw new Error("HTTP error! status: 500");
      },
      messages: async () => ({
        data: [
          {
            info: {
              role: "assistant",
              error: {
                name: "APIError",
                data: { message: "gateway rejected tools with status 422" },
              },
            },
          },
        ],
      }),
    },
  };

  await assert.rejects(
    promptWithDiagnostics(client, "ses_123", "/workspace", {}),
    /gateway rejected tools with status 422/,
  );
});

test("promptWithTransportRecovery resumes the same session after fetch failure", async () => {
  const bodies = [];
  const client = {
    session: {
      prompt: async ({ body }) => {
        bodies.push(body);
        if (bodies.length === 1) throw new Error("fetch failed");
        return {
          data: {
            info: { modelID: "test-model", tokens: {} },
            parts: [{ type: "text", text: "Finished after recovery" }],
          },
        };
      },
      messages: async () => ({ data: [] }),
    },
  };
  const original = {
    agent: "bifrost-builder",
    model: { providerID: "bifrost", modelID: "test-model" },
    parts: [{ type: "text", text: "Build the app" }],
  };
  let runtimeRecoveries = 0;

  const result = await promptWithTransportRecovery(
    client,
    "ses_123",
    "/workspace",
    original,
    {
      recoverClient: async () => {
        runtimeRecoveries += 1;
        return client;
      },
    },
  );

  assert.equal(result.parts[0].text, "Finished after recovery");
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0], original);
  assert.equal(bodies[1].agent, "bifrost-builder");
  assert.equal(bodies[1].model.modelID, "test-model");
  assert.match(bodies[1].parts[0].text, /Continue the existing task/);
  assert.doesNotMatch(bodies[1].parts[0].text, /Build the app/);
  assert.equal(runtimeRecoveries, 1);
});

test("promptWithTransportRecovery does not retry deterministic failures", async () => {
  let prompts = 0;
  const client = {
    session: {
      prompt: async () => {
        prompts += 1;
        throw new Error("unprocessable entity");
      },
      messages: async () => ({ data: [] }),
    },
  };

  await assert.rejects(
    promptWithTransportRecovery(client, "ses_123", "/workspace", {}),
    /unprocessable entity/,
  );
  assert.equal(prompts, 1);
  assert.equal(isRecoverableTransportFailure(new Error("fetch failed")), true);
  assert.equal(isRecoverableTransportFailure(new Error("rate limited")), false);
});

test("promptWithTransportRecovery bounds repeated fetch failures", async () => {
  let prompts = 0;
  const client = {
    session: {
      prompt: async () => {
        prompts += 1;
        throw new Error("fetch failed");
      },
      messages: async () => ({ data: [] }),
    },
  };

  await assert.rejects(
    promptWithTransportRecovery(client, "ses_123", "/workspace", {}),
    /exhausted after 3 attempts/,
  );
  assert.equal(prompts, 4);
});
