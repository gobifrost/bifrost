import { getSandbox } from "@cloudflare/sandbox";
import { WorkflowEntrypoint } from "cloudflare:workers";
import { NonRetryableError } from "cloudflare:workflows";

export { Sandbox } from "@cloudflare/sandbox";
import {
  boundedTimeoutSeconds,
  reportTerminalWorkflowFailure,
  runnerPollCount,
  runnerProcessTerminal,
  runnerReportedTerminalResult,
  sandboxCommandFailure,
  sandboxControlStepConfig,
  sandboxIdForPayload,
  sandboxStepConfig,
} from "./runtime_helpers.mjs";

const RUNNER_PROCESS_ID = "bifrost-runner";
const RUNNER_POLL_SECONDS = 10;
const MAX_CONSECUTIVE_POLL_ERRORS = 6;

function getRunnerSandbox(env, sandboxId) {
  return getSandbox(env.Sandbox, sandboxId, {
    normalizeId: true,
    enableDefaultSession: false,
    keepAlive: true,
  });
}

function processSnapshot(process) {
  return {
    id: process.id,
    status: process.status,
    exitCode: process.exitCode,
  };
}

async function destroyRunnerSandbox(step, env, sandboxId, stepName) {
  try {
    await step.do(stepName, sandboxControlStepConfig(), async () => {
      await getRunnerSandbox(env, sandboxId).destroy();
      return { destroyed: true };
    });
  } catch {
    // A terminal callback is already authoritative. Cleanup receives the same
    // control-plane retries as polling but must not overwrite that result.
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
    const timeoutSeconds = probe ? 120 : boundedTimeoutSeconds(payload);
    const sandboxId = sandboxIdForPayload(payload);

    if (probe) {
      return reportTerminalWorkflowFailure(payload, () =>
        step.do(
          "verify Bifrost runner",
          sandboxStepConfig(timeoutSeconds),
          async () => {
            const sandbox = getSandbox(this.env.Sandbox, sandboxId, {
              normalizeId: true,
              sleepAfter: "10m",
            });
            try {
              const result = await sandbox.exec("bifrost-sandbox-runner --probe", {
                timeout: timeoutSeconds * 1000,
              });
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
      try {
        const started = await step.do(
          "start Bifrost sandbox runner",
          sandboxControlStepConfig(),
          async () => {
            const sandbox = getRunnerSandbox(this.env, sandboxId);
            await sandbox.writeFile(
              "/work/bifrost-job.json",
              JSON.stringify(payload),
            );
            const existing = await sandbox.getProcess(RUNNER_PROCESS_ID);
            if (existing) return processSnapshot(existing);
            const process = await sandbox.startProcess(
              "bifrost-sandbox-runner /work/bifrost-job.json",
              {
                processId: RUNNER_PROCESS_ID,
                autoCleanup: false,
                timeout: timeoutSeconds * 1000,
              },
            );
            return processSnapshot(process);
          },
        );

        let snapshot = started;
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
                  const sandbox = getRunnerSandbox(this.env, sandboxId);
                  const process = await sandbox.getProcess(RUNNER_PROCESS_ID);
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
            async () => {
              const sandbox = getRunnerSandbox(this.env, sandboxId);
              return sandbox.getProcessLogs(RUNNER_PROCESS_ID);
            },
          );
          await destroyRunnerSandbox(
            step,
            this.env,
            sandboxId,
            "destroy completed Bifrost sandbox",
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
        await destroyRunnerSandbox(
          step,
          this.env,
          sandboxId,
          "destroy failed Bifrost sandbox",
        );
        throw error;
      }
    });
  }
}
