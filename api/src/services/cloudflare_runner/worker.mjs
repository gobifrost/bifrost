import { getSandbox } from "@cloudflare/sandbox";
import { WorkflowEntrypoint } from "cloudflare:workers";

export { Sandbox } from "@cloudflare/sandbox";
import {
  boundedTimeoutSeconds,
  reportLaunchFailure,
} from "./runtime_helpers.mjs";

export class BifrostBuilderWorkflow extends WorkflowEntrypoint {
  async run(event, step) {
    const payload = event.payload ?? {};
    const probe = payload.mode === "probe";
    const timeoutSeconds = probe ? 120 : boundedTimeoutSeconds(payload);
    const sandboxId = probe
      ? `bifrost-probe-${String(payload.probe_id ?? "setup")}`
      : `bifrost-${payload.job_id}-${payload.dispatch_attempt}`;

    return step.do(
      probe ? "verify Bifrost runner" : "run Bifrost sandbox job",
      {
        retries: { limit: 0, delay: "1 second", backoff: "constant" },
        timeout: `${Math.ceil(timeoutSeconds / 60) + 2} minutes`,
      },
      async () => {
        const sandbox = getSandbox(this.env.Sandbox, sandboxId, {
          normalizeId: true,
          sleepAfter: "10m",
        });
        try {
          if (probe) {
            const result = await sandbox.exec("bifrost-sandbox-runner --probe", {
              timeout: timeoutSeconds * 1000,
            });
            if (!result.success) {
              throw new Error("Bifrost runner image did not pass its self-test");
            }
            return { ok: true, output: result.stdout.trim().slice(0, 500) };
          }

          await sandbox.writeFile(
            "/workspace/bifrost-job.json",
            JSON.stringify(payload),
          );
          const result = await sandbox.exec(
            "bifrost-sandbox-runner /workspace/bifrost-job.json",
            { timeout: timeoutSeconds * 1000 },
          );
          if (!result.success) {
            throw new Error("Bifrost sandbox runner reported a failed job");
          }
          return { ok: true };
        } catch (error) {
          if (!probe) {
            await reportLaunchFailure(payload, error);
          }
          throw error;
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
    );
  }
}
