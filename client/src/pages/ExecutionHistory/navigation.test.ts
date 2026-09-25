import { describe, expect, it } from "vitest";
import {
	createExecutionHistoryOriginState,
	createExecutionHistoryRestoreState,
	readExecutionHistoryOrigin,
	readExecutionHistoryRestore,
} from "./navigation";

describe("execution history navigation state", () => {
	it("round-trips a history URL and scroll offset for detail navigation", () => {
		const origin = {
			href: "/history?status=Failed&execution=run-1",
			scrollTop: 384,
		};

		expect(
			readExecutionHistoryOrigin(
				createExecutionHistoryOriginState(origin),
			),
		).toEqual(origin);
		expect(
			readExecutionHistoryRestore(
				createExecutionHistoryRestoreState(origin),
			),
		).toEqual(origin);
	});

	it("rejects malformed or non-list return targets", () => {
		expect(
			readExecutionHistoryOrigin({
				executionHistoryOrigin: {
					href: "/history/run-1",
					scrollTop: 4,
				},
			}),
		).toBeNull();
		expect(
			readExecutionHistoryRestore({
				executionHistoryRestore: { href: "/history", scrollTop: -1 },
			}),
		).toBeNull();
	});
});
