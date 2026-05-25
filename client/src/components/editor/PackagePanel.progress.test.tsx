/**
 * Unit tests for the package-install progress collapse + completion logic.
 *
 * Tests the handleProgressEvent helper directly — no component rendering
 * needed, which keeps these fast and dependency-free.
 */

import { describe, it, expect, vi } from "vitest";
import { handleProgressEvent } from "./packageProgress";
import type { ProgressState, ProgressStore } from "./packageProgress";
import type { PackageProgress } from "@/services/websocket";

function makeProgress(overrides: Partial<PackageProgress> = {}): PackageProgress {
	return {
		action: "install",
		line: "Installing on 1/6 workers…",
		total: 6,
		installing: 1,
		installed: 0,
		recycling: 0,
		recycled: 0,
		failed: 0,
		failures: [],
		...overrides,
	};
}

function makeStore(): ProgressStore & {
	appendLogCalls: Parameters<ProgressStore["appendLog"]>[];
	completeExecutionCalls: Parameters<ProgressStore["completeExecution"]>[];
} {
	const appendLogCalls: Parameters<ProgressStore["appendLog"]>[] = [];
	const completeExecutionCalls: Parameters<ProgressStore["completeExecution"]>[] = [];
	return {
		appendLogCalls,
		completeExecutionCalls,
		appendLog: vi.fn((...args: Parameters<ProgressStore["appendLog"]>) => {
			appendLogCalls.push(args);
		}),
		completeExecution: vi.fn(
			(...args: Parameters<ProgressStore["completeExecution"]>) => {
				completeExecutionCalls.push(args);
			},
		),
	};
}

describe("handleProgressEvent — collapse logic", () => {
	it("two progress events with the same line result in only ONE appended log", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();
		const id = "install-1";

		const p = makeProgress({ line: "Installing on 3/6 workers…" });

		handleProgressEvent(p, state, store, id);
		handleProgressEvent(p, state, store, id);

		expect(store.appendLogCalls).toHaveLength(1);
		expect(store.appendLogCalls[0][1].message).toBe("Installing on 3/6 workers…");
	});

	it("a progress event with a different line appends a second log", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();
		const id = "install-1";

		handleProgressEvent(
			makeProgress({ line: "Installing on 3/6 workers…" }),
			state,
			store,
			id,
		);
		handleProgressEvent(
			makeProgress({ line: "Recycling on 3/6 workers…" }),
			state,
			store,
			id,
		);

		expect(store.appendLogCalls).toHaveLength(2);
		expect(store.appendLogCalls[0][1].message).toBe("Installing on 3/6 workers…");
		expect(store.appendLogCalls[1][1].message).toBe("Recycling on 3/6 workers…");
	});
});

describe("handleProgressEvent — completion logic", () => {
	it("marks execution Success when recycled + failed >= total and failed === 0", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();
		const id = "install-1";

		const triggered = handleProgressEvent(
			makeProgress({ total: 6, recycled: 6, failed: 0, line: "Done" }),
			state,
			store,
			id,
		);

		expect(triggered).toBe(true);
		expect(store.completeExecutionCalls).toHaveLength(1);
		expect(store.completeExecutionCalls[0]).toEqual([id, undefined, "Success"]);
	});

	it("marks execution Failed when failed > 0 and recycled + failed >= total", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();
		const id = "install-1";

		const triggered = handleProgressEvent(
			makeProgress({ total: 6, recycled: 4, failed: 2, line: "Done" }),
			state,
			store,
			id,
		);

		expect(triggered).toBe(true);
		expect(store.completeExecutionCalls[0]).toEqual([id, undefined, "Failed"]);
	});

	it("does NOT complete when recycled + failed < total", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();

		handleProgressEvent(
			makeProgress({ total: 6, recycled: 3, failed: 0, line: "In progress" }),
			state,
			store,
			"install-1",
		);

		expect(store.completeExecutionCalls).toHaveLength(0);
	});

	it("only completes once even if multiple events satisfy the condition", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();
		const id = "install-1";

		// Two terminal events with DIFFERENT lines (so both pass the collapse
		// guard and append naturally) — the completionHandled guard must still
		// fire completeExecution exactly once.
		handleProgressEvent(
			makeProgress({ total: 6, recycled: 6, failed: 0, line: "All 6 workers recycled" }),
			state,
			store,
			id,
		);
		handleProgressEvent(
			makeProgress({ total: 6, recycled: 6, failed: 0, line: "Installation complete" }),
			state,
			store,
			id,
		);

		expect(store.appendLogCalls).toHaveLength(2);
		expect(store.completeExecutionCalls).toHaveLength(1);
	});

	it("uses WARNING level when failed > 0", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();

		handleProgressEvent(
			makeProgress({ failed: 1, line: "1 failure" }),
			state,
			store,
			"install-1",
		);

		expect(store.appendLogCalls[0][1].level).toBe("WARNING");
	});

	it("uses INFO level when failed === 0", () => {
		const state: ProgressState = { lastLine: "", completionHandled: false };
		const store = makeStore();

		handleProgressEvent(
			makeProgress({ failed: 0, line: "All good" }),
			state,
			store,
			"install-1",
		);

		expect(store.appendLogCalls[0][1].level).toBe("INFO");
	});
});
