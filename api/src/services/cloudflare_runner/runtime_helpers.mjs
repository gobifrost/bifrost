const MAX_TIMEOUT_SECONDS = 2 * 60 * 60;
const FAILURE_CALLBACK_TIMEOUT_MS = 10_000;
const MAX_COMMAND_ERROR_CHARS = 2_000;
export const WORKSPACE_BROKER_HOST = "workspace.bifrost.internal";

export function boundedTimeoutSeconds(payload) {
  const requested = Number(payload?.timeout_seconds ?? 300);
  if (!Number.isFinite(requested)) return 300;
  return Math.max(30, Math.min(MAX_TIMEOUT_SECONDS, Math.floor(requested)));
}

export function sandboxStepConfig(timeoutSeconds) {
  return {
    // Cloudflare may interrupt Sandbox RPC while rolling out its managed
    // runtime. A second durable attempt recreates the same fenced workspace;
    // deterministic runner exit codes are marked NonRetryable by the Worker.
    retries: { limit: 2, delay: "5 seconds", backoff: "exponential" },
    timeout: `${Math.ceil(timeoutSeconds / 60) + 2} minutes`,
  };
}

export function sandboxControlStepConfig() {
  return {
    retries: { limit: 2, delay: "5 seconds", backoff: "exponential" },
    timeout: "2 minutes",
  };
}

export function runnerPollCount(timeoutSeconds, intervalSeconds = 10) {
  return Math.ceil(timeoutSeconds / intervalSeconds) + 12;
}

export function runnerProcessTerminal(status) {
  return ["completed", "failed", "killed", "error"].includes(status);
}

export async function reportTerminalWorkflowFailure(payload, operation) {
  try {
    return await operation();
  } catch (error) {
    if (payload?.mode !== "probe") {
      await reportLaunchFailure(payload, error);
    }
    throw error;
  }
}

export function isRetryableWorkflowFailure(error) {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /Durable Object reset because its code was updated\.?/i.test(message);
}

export function isContainerStartingError(error) {
  return (
    error instanceof Error &&
    error.name === "SandboxError" &&
    error.message === "Container is starting. Please retry in a moment."
  );
}

export function sandboxIdForPayload(payload) {
  return runnerSandboxIdForPayload(payload);
}

export function baseSandboxIdForPayload(payload) {
  if (payload?.mode === "probe") {
    return String(payload.probe_id ?? "bifrost-probe-setup");
  }
  return `bifrost-${payload?.job_id}-${payload?.dispatch_attempt}`;
}

export function runnerSandboxIdForPayload(payload) {
  const explicit = payload?.runner_sandbox_id;
  if (typeof explicit === "string" && explicit) return explicit;
  if (payload?.mode === "probe") return baseSandboxIdForPayload(payload);
  return `${baseSandboxIdForPayload(payload)}-runner`;
}

export function workspaceSandboxIdForPayload(payload) {
  const explicit = payload?.workspace_sandbox_id;
  if (typeof explicit === "string" && explicit) return explicit;
  return `${baseSandboxIdForPayload(payload)}-workspace`;
}

export function workspaceBrokerUrlForPayload(payload) {
  const explicit = payload?.workspace_broker_url;
  if (typeof explicit === "string" && explicit) return explicit;
  return `http://${WORKSPACE_BROKER_HOST}`;
}

export function hostFromHttpUrl(value) {
  try {
    const parsed = new URL(String(value));
    if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
      return null;
    }
    return parsed.hostname;
  } catch {
    return null;
  }
}

export function uniqueHosts(hosts) {
  return Array.from(
    new Set(
      (hosts ?? [])
        .filter((host) => typeof host === "string")
        .map((host) => host.trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

export function initialRunnerAllowedHosts(payload) {
  return uniqueHosts([
    WORKSPACE_BROKER_HOST,
    hostFromHttpUrl(payload?.callback_base_url),
    ...(Array.isArray(payload?.runner_allowed_hosts)
      ? payload.runner_allowed_hosts
      : []),
  ]);
}

export function buildRunnerAllowedHosts(payload) {
  return uniqueHosts([
    hostFromHttpUrl(payload?.callback_base_url),
    "registry.npmjs.org",
  ]);
}

export function workspaceAllowedHosts(payload) {
  return uniqueHosts(
    Array.isArray(payload?.workspace_allowed_hosts)
      ? payload.workspace_allowed_hosts
      : [],
  );
}

export function workspaceSandboxIdForRunnerSandboxId(runnerSandboxId) {
  const id = String(runnerSandboxId ?? "");
  if (!id.endsWith("-runner")) return null;
  return `${id.slice(0, -"-runner".length)}-workspace`;
}

export function sandboxCommandFailure(result, fallback) {
  const exitCode = Number.isInteger(result?.exitCode)
    ? ` (exit ${result.exitCode})`
    : "";
  const raw =
    (typeof result?.stderr === "string" && result.stderr.trim()) ||
    (typeof result?.stdout === "string" && result.stdout.trim()) ||
    "";
  const redacted = raw
    .replace(/Bearer\s+\S+/gi, "Bearer [redacted]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[redacted token]");
  const detail = redacted.slice(-MAX_COMMAND_ERROR_CHARS);
  return detail ? `${fallback}${exitCode}: ${detail}` : `${fallback}${exitCode}`;
}

export function runnerReportedTerminalResult(result) {
  return result?.exitCode === 1 || result?.exitCode === 2;
}

export async function reportLaunchFailure(payload, error) {
  if (!payload?.callback_base_url || !payload?.job_id || !payload?.capability) {
    return false;
  }
  const message = error instanceof Error ? error.message : "Sandbox launch failed";
  try {
    const response = await fetch(
      `${String(payload.callback_base_url).replace(/\/$/, "")}/api/internal/sandbox/jobs/${payload.job_id}/complete`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${payload.capability}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          status: "failed",
          error: message.slice(0, 4000),
          ...(isRetryableWorkflowFailure(error) ? { retryable: true } : {}),
        }),
        signal: AbortSignal.timeout(FAILURE_CALLBACK_TIMEOUT_MS),
      },
    );
    return response.ok;
  } catch {
    // This callback is best-effort because the original launch error remains
    // the authoritative Workflow failure and must never be masked by a
    // transient Bifrost callback outage.
    return false;
  }
}
