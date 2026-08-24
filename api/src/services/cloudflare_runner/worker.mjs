import {
  ContainerProxy as CloudflareContainerProxy,
  getSandbox,
  Sandbox as CloudflareSandbox,
  streamFile,
} from "@cloudflare/sandbox";
import { WorkflowEntrypoint } from "cloudflare:workers";
import { NonRetryableError } from "cloudflare:workflows";

import {
  boundedTimeoutSeconds,
  buildRunnerAllowedHosts,
  initialRunnerAllowedHosts,
  isContainerStartingError,
  reportTerminalWorkflowFailure,
  runnerPollCount,
  runnerProcessTerminal,
  runnerReportedTerminalResult,
  sandboxCommandFailure,
  sandboxControlStepConfig,
  sandboxStepConfig,
  runnerSandboxIdForPayload,
  workspaceAllowedHosts,
  workspaceBrokerUrlForPayload,
  workspaceSandboxIdForPayload,
  WORKSPACE_BROKER_HOST,
} from "./runtime_helpers.mjs";

const RUNNER_PROCESS_ID = "bifrost-runner";
const RUNNER_POLL_SECONDS = 10;
const MAX_CONSECUTIVE_POLL_ERRORS = 6;
const PROBE_TIMEOUT_SECONDS = 10 * 60;
const WORKSPACE_ROOT = "/work/workspace";
const WORKSPACE_TOOL_TIMEOUT_MS = 120_000;

function decodedSandboxFileStream(stream) {
  const iterator = streamFile(stream)[Symbol.asyncIterator]();
  const encoder = new TextEncoder();
  return new ReadableStream({
    async pull(controller) {
      const { done, value } = await iterator.next();
      if (done) {
        controller.close();
        return;
      }
      controller.enqueue(
        value instanceof Uint8Array ? value : encoder.encode(value),
      );
    },
    async cancel() {
      await iterator.return?.();
    },
  });
}

function getRunnerSandbox(env, sandboxId) {
  return getSandbox(env.Sandbox, sandboxId, {
    normalizeId: true,
    enableDefaultSession: false,
    keepAlive: true,
    transport: "rpc",
  });
}

function getWorkspaceSandbox(env, sandboxId) {
  return getSandbox(env.Sandbox, sandboxId, {
    normalizeId: true,
    enableDefaultSession: false,
    keepAlive: true,
    transport: "rpc",
  });
}

function processSnapshot(process) {
  return {
    id: process.id,
    status: process.status,
    exitCode: process.exitCode,
  };
}

async function destroySandbox(step, env, sandboxId, stepName) {
  try {
    await step.do(stepName, sandboxControlStepConfig(), async () => {
      await getSandbox(env.Sandbox, sandboxId, {
        normalizeId: true,
        enableDefaultSession: false,
        keepAlive: true,
        transport: "rpc",
      }).destroy();
      return { destroyed: true };
    });
  } catch {
    // A terminal callback is already authoritative. Cleanup receives the same
    // control-plane retries as polling but must not overwrite that result.
  }
}

async function destroyRunnerSandbox(step, env, sandboxId, stepName) {
  await destroySandbox(step, env, sandboxId, stepName);
}

async function destroyTurnSandboxes(step, env, payload, stepName) {
  const runnerId = runnerSandboxIdForPayload(payload);
  const workspaceId = workspaceSandboxIdForPayload(payload);
  await destroySandbox(step, env, runnerId, `${stepName} runner`);
  await destroySandbox(step, env, workspaceId, `${stepName} workspace`);
}

function jsonResponse(body, init) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
}

function isValidHost(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 253 &&
    !/[/:?#\s]/.test(value)
  );
}

async function collectBody(request, maxBytes) {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!value) continue;
    total += value.byteLength;
    if (total > maxBytes) {
      throw new Error("request body exceeds workspace broker limit");
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

function boundedBodyStream(request, maxBytes) {
  let total = 0;
  return request.body?.pipeThrough(
    new TransformStream({
      transform(chunk, controller) {
        total += chunk.byteLength;
        if (total > maxBytes) {
          throw new Error("request body exceeds workspace broker limit");
        }
        controller.enqueue(chunk);
      },
    })
  ) ?? new ReadableStream({
    start(controller) {
      controller.close();
    },
  });
}

async function runWorkspaceCommand(sandbox, command, timeout = WORKSPACE_TOOL_TIMEOUT_MS) {
  const result = await sandbox.exec(command, {
    cwd: "/opt/bifrost-build",
    timeout,
  });
  if (!result.success) {
    throw new Error(
      sandboxCommandFailure(result, "Bifrost workspace sandbox command failed"),
    );
  }
  return result;
}

async function handleWorkspaceBroker(request, env, ctx) {
  const runnerSandboxId = ctx.params?.runnerSandboxId;
  const workspaceSandboxId = ctx.params?.workspaceSandboxId;
  if (
    typeof runnerSandboxId !== "string" ||
    !runnerSandboxId ||
    typeof workspaceSandboxId !== "string" ||
    !workspaceSandboxId
  ) {
    return jsonResponse(
      { error: "workspace broker is available only to runner sandboxes" },
      { status: 403 },
    );
  }
  const url = new URL(request.url);
  const workspace = getWorkspaceSandbox(env, workspaceSandboxId);

  if (request.method === "POST" && url.pathname === "/runner-egress") {
    const body = await request.json();
    const hosts = Array.isArray(body?.allowed_hosts) ? body.allowed_hosts : [];
    if (!hosts.every(isValidHost)) {
      return jsonResponse({ error: "invalid allowed host" }, { status: 422 });
    }
    const runner = getRunnerSandbox(env, runnerSandboxId);
    await runner.setAllowedHosts(
      Array.from(new Set([WORKSPACE_BROKER_HOST, ...hosts])),
    );
    return jsonResponse({ ok: true });
  }

  if (request.method === "POST" && url.pathname === "/hydrate") {
    const expected = url.searchParams.get("expected_sha256") ?? "";
    const solutionId = url.searchParams.get("solution_id") ?? "";
    await workspace.mkdir("/work", { recursive: true });
    await workspace.writeFile(
      "/work/input.zip",
      boundedBodyStream(request, 256 * 1024 * 1024),
    );
    await runWorkspaceCommand(
      workspace,
      [
        "bifrost-sandbox-runner",
        "--workspace-hydrate",
        "/work/input.zip",
        "--workspace",
        WORKSPACE_ROOT,
        "--expected-sha256",
        expected,
        "--solution-id",
        solutionId,
      ].join(" "),
      120_000,
    );
    return jsonResponse({ ok: true });
  }

  if (request.method === "POST" && url.pathname === "/tool") {
    const requestId = crypto.randomUUID();
    const requestPath = `/work/tool-request-${requestId}.json`;
    const responsePath = `/work/tool-response-${requestId}.json`;
    const requestBytes = await collectBody(request, 2 * 1024 * 1024);
    await workspace.mkdir("/work", { recursive: true });
    await workspace.writeFile(
      requestPath,
      new Response(requestBytes).body,
    );
    try {
      await runWorkspaceCommand(
        workspace,
        [
          "bifrost-sandbox-runner",
          "--workspace-tool",
          requestPath,
          "--workspace",
          WORKSPACE_ROOT,
          "--output",
          responsePath,
        ].join(" "),
      );
      const response = await workspace.readFile(responsePath);
      return new Response(response.content, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    } finally {
      await Promise.allSettled([
        workspace.deleteFile(requestPath),
        workspace.deleteFile(responsePath),
      ]);
    }
  }

  if (request.method === "GET" && url.pathname === "/archive") {
    await runWorkspaceCommand(
      workspace,
      [
        "bifrost-sandbox-runner",
        "--workspace-archive",
        WORKSPACE_ROOT,
        "--output",
        "/work/output.zip",
        "--digest-output",
        "/work/output.sha256",
      ].join(" "),
      120_000,
    );
    const digest = await workspace.readFile("/work/output.sha256");
    const stream = await workspace.readFileStream("/work/output.zip");
    return new Response(decodedSandboxFileStream(stream), {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "X-Bifrost-Sha256": digest.content.trim(),
      },
    });
  }

  return jsonResponse({ error: "unknown workspace broker route" }, { status: 404 });
}

// The containers package stores static outbound handlers in an isolate-local
// registry. ContainerProxy runs in a separate WorkerEntrypoint isolate, so the
// SDK's default proxy cannot see Sandbox.outboundByHost for application-defined
// handlers. Route Bifrost's one private host explicitly while retaining the
// SDK proxy for its built-in mount and normal egress behavior.
export class ContainerProxy extends CloudflareContainerProxy {
  async fetch(request) {
    if (new URL(request.url).hostname === WORKSPACE_BROKER_HOST) {
      const override =
        this.ctx.props.outboundByHostOverrides?.[WORKSPACE_BROKER_HOST];
      return handleWorkspaceBroker(request, this.env, {
        containerId: this.ctx.props.containerId,
        params: override?.params,
      });
    }
    return super.fetch(request);
  }
}

export class Sandbox extends CloudflareSandbox {
  // The trusted harness needs HTTPS access to Bifrost, the configured model,
  // and package registries. Cloudflare's HTTPS interception currently causes
  // the managed Sandbox sidecar to exit before its control port is ready, so
  // the runner uses direct egress. User-authored commands execute in the
  // separate workspace sandbox, which is still started with internet disabled.
  enableInternet = true;
  allowedHosts = [];
}

// Use assignment so the inherited Container setter registers the named
// handler. Native static class fields define an own property and bypass that
// setter, leaving runtime setOutboundByHost calls unable to resolve the name.
Sandbox.outboundHandlers = {
  bifrostWorkspaceBroker: handleWorkspaceBroker,
};

async function prepareTurnSandboxes(env, payload) {
  const runnerId = runnerSandboxIdForPayload(payload);
  const workspaceId = workspaceSandboxIdForPayload(payload);
  const runner = getRunnerSandbox(env, runnerId);
  const workspace = getWorkspaceSandbox(env, workspaceId);
  await runner.start({ enableInternet: true });
  await runner.setAllowedHosts(initialRunnerAllowedHosts(payload));
  await runner.setOutboundByHost(
    WORKSPACE_BROKER_HOST,
    "bifrostWorkspaceBroker",
    { runnerSandboxId: runnerId, workspaceSandboxId: workspaceId },
  );
  await workspace.start({ enableInternet: false });
  await workspace.setAllowedHosts(workspaceAllowedHosts(payload));
  return { runner, runnerId, workspaceId };
}

async function writeRunnerEnvelope(sandbox, payload) {
  const runnerPayload =
    payload.job_type === "solution.builder.turn"
      ? {
          ...payload,
          runner_sandbox_id: runnerSandboxIdForPayload(payload),
          workspace_sandbox_id: workspaceSandboxIdForPayload(payload),
          workspace_broker_url: workspaceBrokerUrlForPayload(payload),
        }
      : payload;
  await sandbox.writeFile(
    "/work/bifrost-job.json",
    JSON.stringify(runnerPayload),
  );
}

async function startRunnerProcess(sandbox, timeoutSeconds) {
  const existing = await sandbox.getProcess(RUNNER_PROCESS_ID);
  if (existing) return processSnapshot(existing);
  const process = await sandbox.startProcess(
    "bifrost-sandbox-runner /work/bifrost-job.json",
    {
      processId: RUNNER_PROCESS_ID,
      autoCleanup: false,
      timeout: timeoutSeconds * 1000,
      cwd: "/opt/bifrost-build",
    },
  );
  return processSnapshot(process);
}

async function startSandboxRunner(env, payload, timeoutSeconds) {
  if (payload.job_type === "solution.builder.turn") {
    const { runner, runnerId } = await prepareTurnSandboxes(env, payload);
    await writeRunnerEnvelope(runner, payload);
    return {
      sandboxId: runnerId,
      snapshot: await startRunnerProcess(runner, timeoutSeconds),
    };
  }
  const sandboxId = runnerSandboxIdForPayload(payload);
  const sandbox = getRunnerSandbox(env, sandboxId);
  await sandbox.start({ enableInternet: true });
  await sandbox.setAllowedHosts(buildRunnerAllowedHosts(payload));
  await writeRunnerEnvelope(sandbox, payload);
  return {
    sandboxId,
    snapshot: await startRunnerProcess(sandbox, timeoutSeconds),
  };
}

async function readRunnerLogs(env, sandboxId) {
  const sandbox = getRunnerSandbox(env, sandboxId);
  return sandbox.getProcessLogs(RUNNER_PROCESS_ID);
}

async function getRunnerProcess(env, sandboxId) {
  const sandbox = getRunnerSandbox(env, sandboxId);
  return sandbox.getProcess(RUNNER_PROCESS_ID);
}

async function destroyCompletedSandboxes(step, env, payload, sandboxId) {
  if (payload.job_type === "solution.builder.turn") {
    await destroyTurnSandboxes(step, env, payload, "destroy completed Bifrost sandbox");
    return;
  }
  await destroyRunnerSandbox(
    step,
    env,
    sandboxId,
    "destroy completed Bifrost sandbox",
  );
}

async function destroyFailedSandboxes(step, env, payload, sandboxId) {
  if (payload.job_type === "solution.builder.turn") {
    await destroyTurnSandboxes(step, env, payload, "destroy failed Bifrost sandbox");
    return;
  }
  try {
    await destroyRunnerSandbox(step, env, sandboxId, "destroy failed Bifrost sandbox");
  } catch {
  }
}

// Wrangler requires a default export to classify this as an ES Module Worker.
// The Worker has no public HTTP surface; Workflows are started through the API.
export default {
  fetch() {
    return new Response(null, { status: 404 });
  },
};

export class BifrostBuilderWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const payload = event.payload ?? {};
    const probe = payload.mode === "probe";
    const timeoutSeconds = probe
      ? PROBE_TIMEOUT_SECONDS
      : boundedTimeoutSeconds(payload);
    const sandboxId = runnerSandboxIdForPayload(payload);

    if (probe) {
      return reportTerminalWorkflowFailure(payload, () =>
        step.do(
          "verify Bifrost runner",
          sandboxStepConfig(timeoutSeconds),
          async () => {
            const sandbox = getSandbox(this.env.Sandbox, sandboxId, {
              normalizeId: true,
              sleepAfter: "10m",
              transport: "rpc",
            });
            try {
              let result;
              try {
                result = await sandbox.exec("bifrost-sandbox-runner --probe", {
                  timeout: timeoutSeconds * 1000,
                });
              } catch (error) {
                // Cloudflare reports a not-yet-distributed container image
                // only after its own start wait. End this Workflow attempt so
                // the provisioning job can back off and start a fresh probe
                // instead of pinning retries to the same cold sandbox.
                if (isContainerStartingError(error)) {
                  throw new NonRetryableError(String(error));
                }
                throw error;
              }
              if (!result.success) {
                throw new NonRetryableError(
                  sandboxCommandFailure(
                    result,
                    "Bifrost runner image did not pass its self-test",
                  ),
                );
              }
              return { ok: true, output: result.stdout.trim().slice(0, 500) };
            } finally {
              try {
                await sandbox.destroy();
              } catch {
                // Cleanup failure cannot supersede the runner callback or the
                // original launch error. Cloudflare will still reap an idle
                // sandbox via sleepAfter.
              }
            }
          },
        ),
      );
    }

    return reportTerminalWorkflowFailure(payload, async () => {
      let activeSandboxId = sandboxId;
      try {
        const started = await step.do(
          "start Bifrost sandbox runner",
          sandboxControlStepConfig(),
          async () => startSandboxRunner(this.env, payload, timeoutSeconds),
        );

        activeSandboxId = started.sandboxId;
        let snapshot = started.snapshot;
        let pollErrors = 0;
        const polls = runnerPollCount(timeoutSeconds, RUNNER_POLL_SECONDS);
        for (let poll = 0; poll < polls; poll += 1) {
          if (!runnerProcessTerminal(snapshot.status)) {
            await step.sleep(
              `wait for Bifrost runner ${poll + 1}`,
              `${RUNNER_POLL_SECONDS} seconds`,
            );
            try {
              snapshot = await step.do(
                `check Bifrost runner ${poll + 1}`,
                sandboxControlStepConfig(),
                async () => {
                  const process = await getRunnerProcess(
                    this.env,
                    activeSandboxId,
                  );
                  if (!process) {
                    throw new NonRetryableError(
                      "Cloudflare lost the active Bifrost runner process",
                    );
                  }
                  return processSnapshot(process);
                },
              );
              pollErrors = 0;
            } catch (error) {
              pollErrors += 1;
              if (pollErrors >= MAX_CONSECUTIVE_POLL_ERRORS) throw error;
              continue;
            }
          }

          if (!runnerProcessTerminal(snapshot.status)) continue;
          const logs = await step.do(
            "read Bifrost runner result",
            sandboxControlStepConfig(),
            async () => readRunnerLogs(this.env, activeSandboxId),
          );
          await destroyCompletedSandboxes(
            step,
            this.env,
            payload,
            activeSandboxId,
          );
          const result = { ...logs, exitCode: snapshot.exitCode };
          if (snapshot.exitCode === 0) return { ok: true };
          if (runnerReportedTerminalResult(result)) {
            return {
              ok: true,
              terminalStatus: snapshot.exitCode === 2 ? "cancelled" : "failed",
            };
          }
          throw new NonRetryableError(
            sandboxCommandFailure(
              result,
              "Bifrost sandbox runner reported a failed job",
            ),
          );
        }

        throw new NonRetryableError("Bifrost sandbox runner exceeded its timeout");
      } catch (error) {
        await destroyFailedSandboxes(
          step,
          this.env,
          payload,
          activeSandboxId,
        );
        throw error;
      }
    });
  }
}
