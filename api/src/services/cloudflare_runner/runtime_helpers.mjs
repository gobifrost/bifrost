const MAX_TIMEOUT_SECONDS = 2 * 60 * 60;
const FAILURE_CALLBACK_TIMEOUT_MS = 10_000;

export function boundedTimeoutSeconds(payload) {
  const requested = Number(payload?.timeout_seconds ?? 300);
  if (!Number.isFinite(requested)) return 300;
  return Math.max(30, Math.min(MAX_TIMEOUT_SECONDS, Math.floor(requested)));
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
