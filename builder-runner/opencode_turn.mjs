import { readFile, writeFile } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";

import {
  createOpencodeClient,
  createOpencodeServer,
} from "@opencode-ai/sdk";
import { Agent as UndiciAgent } from "undici";

const MAX_TRANSPORT_RECOVERIES = 3;
const TRANSPORT_TIMEOUT_GRACE_MS = 60_000;
const MAX_DIAGNOSTIC_MESSAGES = 500;
const MAX_DIAGNOSTIC_TOOL_NAMES = 32;
const MAX_NO_ACTIVITY_MS = 5 * 60_000;
const ACTIVITY_POLL_INTERVAL_MS = 5_000;

function requireString(value, name) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

export function describeStructuredError(value) {
  if (!value) return "unknown OpenCode error";
  if (value instanceof Error) {
    const cause = value.cause;
    const causeDetail = cause && cause !== value ? describeStructuredError(cause) : "";
    const code =
      cause && typeof cause === "object" && "code" in cause
        ? String(cause.code)
        : "";
    return [...new Set([value.message, code, causeDetail].filter(Boolean))]
      .join(": ")
      .slice(0, 8_000);
  }
  if (typeof value === "string") return value.slice(0, 8_000);
  if (typeof value === "object") {
    const data = value.data && typeof value.data === "object" ? value.data : {};
    const candidates = [
      data.message,
      data.responseBody,
      value.message,
      value.name,
    ];
    const useful = candidates.filter(
      (candidate) => typeof candidate === "string" && candidate.trim().length > 0,
    );
    if (useful.length > 0) return useful.join(": ").slice(0, 8_000);
    try {
      return JSON.stringify(value).slice(0, 8_000);
    } catch {
      return "unserializable OpenCode error";
    }
  }
  return String(value).slice(0, 8_000);
}

async function promptFailureDetail(client, sessionID, directory, error) {
  let detail = describeStructuredError(error);
  try {
    const messages = await client.session.messages({
      path: { id: sessionID },
      query: { directory, limit: 20 },
    });
    if (Array.isArray(messages.data)) {
      const failed = messages.data
        .map((entry) => entry?.info)
        .reverse()
        .find((info) => info?.role === "assistant" && info.error);
      if (failed?.error) {
        const messageDetail = describeStructuredError(failed.error);
        if (messageDetail && !detail.includes(messageDetail)) {
          detail = `${detail}: ${messageDetail}`;
        }
      }
    }
  } catch {
    // Preserve the original prompt failure if diagnostics are unavailable.
  }
  return detail.slice(0, 8_000);
}

function messageID(entry) {
  return typeof entry?.info?.id === "string" ? entry.info.id : "";
}

export function summarizeSessionMessages(entries, excludedMessageIDs = new Set()) {
  const messages = Array.isArray(entries) ? entries : [];
  const included = messages.filter(
    (entry) => !excludedMessageIDs.has(messageID(entry)),
  );
  const toolCounts = new Map();
  let assistantMessageCount = 0;
  let compactionCount = 0;
  let retryCount = 0;

  for (const entry of included) {
    if (entry?.info?.role === "assistant") assistantMessageCount += 1;
    for (const part of Array.isArray(entry?.parts) ? entry.parts : []) {
      if (part?.type === "compaction") {
        compactionCount += 1;
        continue;
      }
      if (part?.type === "retry") {
        retryCount += 1;
        continue;
      }
      if (part?.type !== "tool") continue;
      const name =
        typeof part.tool === "string" && part.tool.trim()
          ? part.tool.trim().slice(0, 100)
          : "unknown";
      const current = toolCounts.get(name) ?? { count: 0, error_count: 0 };
      current.count += 1;
      if (part.state?.status === "error") current.error_count += 1;
      toolCounts.set(name, current);
    }
  }

  const allTools = [...toolCounts.entries()]
    .map(([name, counts]) => ({ name, ...counts }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name));
  const tools = allTools.slice(0, MAX_DIAGNOSTIC_TOOL_NAMES);
  const summarizedCalls = tools.reduce((total, item) => total + item.count, 0);
  const totalToolCalls = allTools.reduce((total, item) => total + item.count, 0);

  return {
    message_count: included.length,
    assistant_message_count: assistantMessageCount,
    tool_call_count: totalToolCalls,
    tool_error_count: allTools.reduce(
      (total, item) => total + item.error_count,
      0,
    ),
    other_tool_call_count: totalToolCalls - summarizedCalls,
    compaction_count: compactionCount,
    retry_count: retryCount,
    truncated: messages.length >= MAX_DIAGNOSTIC_MESSAGES,
    tools,
  };
}

async function sessionMessages(client, sessionID, directory) {
  const response = await client.session.messages({
    path: { id: sessionID },
    query: { directory, limit: MAX_DIAGNOSTIC_MESSAGES },
    throwOnError: true,
  });
  return Array.isArray(response.data) ? response.data : [];
}

function newestSessionActivity(entries, fallback) {
  let newest = fallback;
  for (const entry of Array.isArray(entries) ? entries : []) {
    const candidates = [
      entry?.info?.time?.created,
      entry?.info?.time?.completed,
    ];
    for (const part of Array.isArray(entry?.parts) ? entry.parts : []) {
      candidates.push(part?.time?.created, part?.time?.start, part?.time?.end);
    }
    for (const candidate of candidates) {
      if (Number.isFinite(candidate)) newest = Math.max(newest, candidate);
    }
  }
  return newest;
}

function waitFor(milliseconds, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export async function promptWithActivityWatchdog(
  client,
  sessionID,
  directory,
  body,
  options = {},
) {
  const timeoutMs = options.timeoutMs ?? MAX_NO_ACTIVITY_MS;
  const pollIntervalMs = options.pollIntervalMs ?? ACTIVITY_POLL_INTERVAL_MS;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1) {
    throw new Error("activity timeout must be a positive integer");
  }
  const stopped = new AbortController();
  let lastActivity = Date.now();
  const watchdog = (async () => {
    while (!stopped.signal.aborted) {
      await waitFor(pollIntervalMs, stopped.signal);
      if (stopped.signal.aborted) return;
      try {
        lastActivity = newestSessionActivity(
          await sessionMessages(client, sessionID, directory),
          lastActivity,
        );
      } catch {
        // A transient loopback diagnostic failure does not reset activity.
      }
      if (Date.now() - lastActivity < timeoutMs) continue;
      try {
        await client.session.abort({
          path: { id: sessionID },
          query: { directory },
          throwOnError: true,
        });
      } catch {
        // Runtime teardown in executeTurn remains the final kill boundary.
      }
      throw new Error(
        `OpenCode made no observable model or tool progress for ${Math.ceil(timeoutMs / 1_000)} seconds`,
      );
    }
  })();

  try {
    return await Promise.race([
      promptWithDiagnostics(client, sessionID, directory, body),
      watchdog,
    ]);
  } finally {
    stopped.abort();
  }
}

async function collectSessionDiagnostics(
  client,
  sessionID,
  directory,
  excludedMessageIDs,
) {
  try {
    return summarizeSessionMessages(
      await sessionMessages(client, sessionID, directory),
      excludedMessageIDs,
    );
  } catch {
    return null;
  }
}

export async function promptWithDiagnostics(
  client,
  sessionID,
  directory,
  body,
) {
  let prompted;
  try {
    prompted = await client.session.prompt({
      path: { id: sessionID },
      query: { directory },
      body,
    });
  } catch (error) {
    const detail = await promptFailureDetail(
      client,
      sessionID,
      directory,
      error,
    );
    throw new Error(`OpenCode prompt failed: ${detail}`);
  }
  if (prompted.error || !prompted.data) {
    const detail = await promptFailureDetail(
      client,
      sessionID,
      directory,
      prompted.error,
    );
    throw new Error(`OpenCode prompt failed: ${detail}`);
  }
  return prompted.data;
}

export function isRecoverableTransportFailure(error) {
  return /(?:^|:\s)fetch failed(?:$|[.:\s])/i.test(
    describeStructuredError(error),
  );
}

export function longRunningTransportTimeoutMs(timeoutSeconds) {
  const requested = Number(timeoutSeconds);
  if (!Number.isInteger(requested) || requested < 1 || requested > 7_200) {
    throw new Error("timeoutSeconds must be an integer between 1 and 7200");
  }
  return requested * 1_000 + TRANSPORT_TIMEOUT_GRACE_MS;
}

async function createLongRunningRuntime(options) {
  const { transportTimeoutMs, ...serverOptions } = options;
  const dispatcher = new UndiciAgent({
    headersTimeout: transportTimeoutMs,
    bodyTimeout: transportTimeoutMs,
  });
  let server;
  try {
    server = await createOpencodeServer(serverOptions);
    const client = createOpencodeClient({
      baseUrl: server.url,
      fetch: (request) => fetch(request, { dispatcher }),
    });
    return {
      client,
      server,
      async close() {
        server.close();
        await dispatcher.close();
      },
    };
  } catch (error) {
    server?.close();
    await dispatcher.close();
    throw error;
  }
}

async function closeRuntime(runtime) {
  if (typeof runtime?.close === "function") {
    await runtime.close();
    return;
  }
  runtime?.server?.close();
}

export async function promptWithTransportRecovery(
  client,
  sessionID,
  directory,
  body,
  options = {},
) {
  let currentBody = body;
  let currentClient = client;
  for (let recovery = 0; ; recovery += 1) {
    try {
      return await promptWithActivityWatchdog(
        currentClient,
        sessionID,
        directory,
        currentBody,
        options.activityWatchdog,
      );
    } catch (error) {
      if (!isRecoverableTransportFailure(error)) {
        if (recovery === 0) throw error;
        throw new Error(
          `OpenCode transport recovery failed: ${describeStructuredError(error)}`,
        );
      }
      if (recovery >= MAX_TRANSPORT_RECOVERIES) {
        throw new Error(
          `OpenCode transport recovery exhausted after ${MAX_TRANSPORT_RECOVERIES} attempts: ` +
            describeStructuredError(error),
        );
      }
      if (typeof options.recoverClient === "function") {
        try {
          currentClient = await options.recoverClient({
            client: currentClient,
            error,
            recovery: recovery + 1,
            sessionID,
          });
        } catch (recoveryError) {
          throw new Error(
            `OpenCode runtime recovery ${recovery + 1} failed: ` +
              describeStructuredError(recoveryError),
          );
        }
      }
    }

    // OpenCode 1.18 does not classify Undici's bare "fetch failed" as a
    // retryable model error. The original user message, partial tool history,
    // and workspace changes are already durable in this session, so append a
    // bounded synthetic continuation instead of replaying the user's turn or
    // rebuilding the workspace from scratch.
    currentBody = {
      ...body,
      parts: [
        {
          type: "text",
          text:
            `A transient network interruption ended the previous model request ` +
            `(recovery ${recovery + 1} of ${MAX_TRANSPORT_RECOVERIES}). ` +
            "Continue the existing task from the current conversation and workspace. " +
            "Do not restart or repeat work that is already complete.",
        },
      ],
    };
  }
}

export function completionFromResponse(response, sessionID, fallbackModel) {
  if (!response || typeof response !== "object") {
    throw new Error("OpenCode returned an invalid response");
  }
  const info = response.info;
  const parts = response.parts;
  if (!info || typeof info !== "object" || !Array.isArray(parts)) {
    throw new Error("OpenCode returned an incomplete response");
  }
  if (info.error) {
    const detail =
      typeof info.error?.data?.message === "string"
        ? info.error.data.message
        : info.error.name || "unknown model error";
    throw new Error(`OpenCode model request failed: ${detail}`);
  }
  const finalText = parts
    .filter(
      (part) =>
        part?.type === "text" &&
        part.ignored !== true &&
        typeof part.text === "string" &&
        part.text.trim().length > 0,
    )
    .map((part) => part.text.trim())
    .join("\n\n");
  if (!finalText) {
    throw new Error("OpenCode completed without a user-facing response");
  }
  const tokens = info.tokens && typeof info.tokens === "object" ? info.tokens : {};
  return {
    status: "succeeded",
    final_text: finalText.slice(0, 100_000),
    tool_call_count: parts.filter((part) => part?.type === "tool").length,
    model: typeof info.modelID === "string" ? info.modelID : fallbackModel,
    token_count_input: Math.max(0, Number(tokens.input) || 0),
    token_count_output: Math.max(0, Number(tokens.output) || 0),
    harness_session_id: sessionID,
  };
}

export async function executeTurn(request, dependencies = {}) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    throw new Error("OpenCode request must be an object");
  }
  const directory = requireString(request.directory, "directory");
  const prompt = requireString(request.prompt, "prompt");
  const model = requireString(request.model, "model");
  const title = requireString(request.title, "title");
  const sessionMarkerPath = requireString(
    request.sessionMarkerPath,
    "sessionMarkerPath",
  );
  if (!isAbsolute(sessionMarkerPath)) {
    throw new Error("sessionMarkerPath must be absolute");
  }
  const transportTimeoutMs = longRunningTransportTimeoutMs(
    request.timeoutSeconds,
  );
  if (!request.config || typeof request.config !== "object") {
    throw new Error("config is required");
  }

  const abortController = new AbortController();
  const stop = () => abortController.abort(new Error("OpenCode turn interrupted"));
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  let runtime;
  let runtimeNumber = 0;
  let sessionID;
  let baselineMessageIDs = new Set();
  const createRuntime =
    dependencies.createRuntime ?? createLongRunningRuntime;
  const startRuntime = async () => {
    runtimeNumber += 1;
    const runtime = await createRuntime({
      config: request.config,
      signal: abortController.signal,
      timeout: 30_000,
      transportTimeoutMs,
      // Each recovery gets a fresh loopback port. The SDK's server.close()
      // signals its child process but does not await port release.
      port: 4_095 + runtimeNumber,
    });
    if (typeof dependencies.onRuntimeStarted === "function") {
      await dependencies.onRuntimeStarted(runtime, runtimeNumber);
    }
    return runtime;
  };
  try {
    runtime = await startRuntime();
    const client = runtime.client;
    let session;
    if (typeof request.sessionID === "string" && request.sessionID.trim()) {
      const restored = await client.session.get({
        path: { id: request.sessionID.trim() },
        query: { directory },
        throwOnError: true,
      });
      session = restored.data;
    } else {
      const created = await client.session.create({
        body: { title },
        query: { directory },
        throwOnError: true,
      });
      session = created.data;
    }
    sessionID = requireString(session?.id, "OpenCode session id");
    await writeFile(
      sessionMarkerPath,
      `${JSON.stringify({ schema_version: 1, session_id: sessionID })}\n`,
      { encoding: "utf8", mode: 0o600 },
    );
    baselineMessageIDs = new Set(
      (await sessionMessages(client, sessionID, directory))
        .map(messageID)
        .filter(Boolean),
    );
    const prompted = await promptWithTransportRecovery(
      client,
      sessionID,
      directory,
      {
        agent: "bifrost-builder",
        model: { providerID: "bifrost", modelID: model },
        parts: [{ type: "text", text: prompt }],
      },
      {
        recoverClient: async () => {
          await closeRuntime(runtime);
          runtime = await startRuntime();
          const restored = await runtime.client.session.get({
            path: { id: sessionID },
            query: { directory },
            throwOnError: true,
          });
          const restoredID = requireString(
            restored.data?.id,
            "restored OpenCode session id",
          );
          if (restoredID !== sessionID) {
            throw new Error("OpenCode restored a different session");
          }
          return runtime.client;
        },
      },
    );
    const completion = completionFromResponse(prompted, sessionID, model);
    completion.harness_diagnostics = await collectSessionDiagnostics(
      runtime.client,
      sessionID,
      directory,
      baselineMessageIDs,
    );
    return completion;
  } catch (error) {
    const enrichedError =
      error instanceof Error ? error : new Error(describeStructuredError(error));
    if (sessionID) {
      enrichedError.harnessSessionID = sessionID;
      enrichedError.harnessDiagnostics = await collectSessionDiagnostics(
        runtime?.client,
        sessionID,
        directory,
        baselineMessageIDs,
      );
    }
    throw enrichedError;
  } finally {
    process.removeListener("SIGINT", stop);
    process.removeListener("SIGTERM", stop);
    await closeRuntime(runtime);
  }
}

async function main(argv) {
  if (argv.length !== 1) {
    throw new Error("usage: node opencode_turn.mjs REQUEST_JSON");
  }
  const request = JSON.parse(await readFile(argv[0], "utf8"));
  const completion = await executeTurn(request);
  process.stdout.write(`${JSON.stringify(completion)}\n`);
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(
      `${JSON.stringify({
        error: describeStructuredError(error).slice(0, 8_000),
        harness_session_id:
          typeof error?.harnessSessionID === "string"
            ? error.harnessSessionID
            : null,
        harness_diagnostics: error?.harnessDiagnostics ?? null,
      })}\n`,
    );
    process.exitCode = 1;
  });
}
