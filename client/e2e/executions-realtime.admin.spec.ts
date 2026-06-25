/**
 * Realtime Execution Streaming Happy Path (Admin)
 *
 * Covers: a user kicks off a workflow execution, navigates to the execution
 * details page while it's still running, and watches logs stream in over
 * WebSocket and the status flip to terminal (Completed) once the workflow
 * finishes. Complementary to executions.admin.spec.ts, which covers the page
 * at rest. The WebSocket merge logic itself is unit-tested in
 * client/src/lib/executionLogs.test.ts.
 */

import { test, expect } from "./fixtures/api-fixture";

// Inline Python workflow that waits briefly before emitting logs so the
// details page can mount and subscribe before the first frame arrives. It then
// emits logs slowly enough to snapshot an initial count and observe more lines
// arrive BEFORE the execution finishes.
// If this script runs too fast, all logs land before the assertion window opens
// and we can't prove they streamed in vs. were fetched at the end.
// `result` is returned as the workflow result.
const SCRIPT = `
import asyncio
import logging

logger = logging.getLogger(__name__)

await asyncio.sleep(5.0)

for i in range(0, 21):
    logger.info(f"streaming log line {i}")
    await asyncio.sleep(0.75)

result = {"ok": True, "lines": 21}
`.trim();

test.describe("Execution Realtime Streaming", () => {
	test("streams logs and status updates live into the details page", async ({
		page,
		api,
	}) => {
		// Script runs ~21s; allow generous headroom for navigation, WebSocket
		// handshake, and the final status/result assertions.
		test.setTimeout(90000);

		// Fail the test on any console error. Attach before navigation so we
		// catch errors from the initial mount, WebSocket handshake, and merge.
		const consoleErrors: string[] = [];
		page.on("console", (msg) => {
			if (msg.type() === "error") {
				consoleErrors.push(msg.text());
			}
		});
		page.on("pageerror", (err) => {
			consoleErrors.push(err.message);
		});

		// Kick off an execution via REST. The API returns the execution_id
		// immediately; the worker runs async, so we have ~2s of runway before
		// it terminates — plenty of time to land on the details page.
		// `code` is plain Python; `run_code()` base64-encodes internally
		// before enqueuing.
		const res = await api.post("/api/workflows/execute", {
			data: {
				workflow_id: null,
				input_data: {},
				form_id: null,
				transient: false,
				code: SCRIPT,
				script_name: "e2e-realtime-streaming",
			},
		});
		expect(res.ok(), await res.text()).toBe(true);
		const body = await res.json();
		const executionId: string = body.execution_id;
		expect(executionId).toBeTruthy();

		// Navigate to the details page while the workflow is still running.
		await page.goto(`/history/${executionId}`);
		await page.waitForURL(new RegExp(`/history/${executionId}`));

		// Assertion 1: at least one log message appears while running.
		// Each emitted line renders as visible text in the logs panel. The
		// WebSocket frames deliver them one-at-a-time, so seeing ANY of them
		// means the streaming pipeline is wired end-to-end.
		const streamingLines = page.getByText(/^streaming log line \d+$/);
		await streamingLines
			.first()
			.waitFor({ state: "visible", timeout: 45000 });

		// Snapshot count of streaming lines visible right after the first
		// arrives. Because the script sleeps 1s between lines, we should see
		// only a handful at this point — far fewer than the full 20.
		const initialLogCount = await streamingLines.count();
		expect(initialLogCount).toBeGreaterThan(0);

		// Assertion 2: log entries grow incrementally via the stream.
		// Poll until more lines have appeared than we saw initially. This
		// happens without a page reload — if it does, the stream is working.
		await expect
			.poll(async () => streamingLines.count(), { timeout: 45000 })
			.toBeGreaterThan(initialLogCount);

		// Assertion 3: status transitions from a pre-terminal state (Running
		// or Pending) to the terminal "Completed" label the status badge uses
		// for Success. The badge lives in the page header.
		const completedBadge = page.getByText("Completed", { exact: true });
		await completedBadge
			.first()
			.waitFor({ state: "visible", timeout: 30000 });

		// Assertion 4: the final result panel renders once complete. The
		// ExecutionResultPanel section is headed by an exact "Result" heading
		// (the restyle replaced the old "Workflow execution result" card
		// description with a small-caps <h4>Result</h4> section header).
		// PrettyInputDisplay converts the JSON keys to Title Case, so "lines"
		// becomes "Lines" — finding it confirms the result payload rendered.
		await expect(
			page.getByRole("heading", { name: "Result", exact: true }),
		).toBeVisible({ timeout: 10000 });
		await expect(page.getByText("Lines", { exact: true })).toBeVisible();

		// Assertion 5: no console errors happened during the run.
		expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
	});
});
