import { describe, expect, it } from "vitest";

import type { HistoryUpdate } from "@/services/websocket";
import {
	mergeExecutionHistoryPage,
	type ExecutionHistoryPage,
} from "./executionHistoryCache";

function row(id: string, startedAt: string) {
	return {
		execution_id: id,
		workflow_name: `workflow-${id}`,
		status: "Success",
		started_at: startedAt,
		created_at: startedAt,
	};
}

function update(id: string, startedAt: string): HistoryUpdate {
	return {
		execution_id: id,
		workflow_name: `workflow-${id}`,
		status: "Running",
		executed_by: "user-1",
		executed_by_name: "Test User",
		started_at: startedAt,
		timestamp: startedAt,
	};
}

function decodeCursor(token: string) {
	const base64 = token.replace(/-/g, "+").replace(/_/g, "/");
	return JSON.parse(globalThis.atob(base64)) as {
		v: number;
		s: string;
		i: string;
	};
}

describe("mergeExecutionHistoryPage", () => {
	it("caps a paginated first page and moves its cursor to the visible tail", () => {
		const page: ExecutionHistoryPage = {
			executions: [
				row("a", "2026-08-27T18:00:00.000000Z"),
				row("b", "2026-08-27T17:59:00.000000Z"),
				row("c", "2026-08-27T17:58:00.000000Z"),
			],
			continuation_token: "old-token",
		};

		const result = mergeExecutionHistoryPage(
			page,
			update("new", "2026-08-27T18:01:00.000000Z"),
			true,
		);

		expect(result.executions.map((execution) => execution.execution_id)).toEqual([
			"new",
			"a",
			"b",
		]);
		expect(decodeCursor(result.continuation_token!)).toEqual({
			v: 1,
			s: "2026-08-27T17:59:00.000000Z",
			i: "b",
		});
	});

	it("updates an existing row without changing the page boundary", () => {
		const page: ExecutionHistoryPage = {
			executions: [row("a", "2026-08-27T18:00:00Z")],
			continuation_token: "server-token",
		};

		const result = mergeExecutionHistoryPage(
			page,
			{ ...update("a", "2026-08-27T18:00:00Z"), status: "Success" },
			true,
		);

		expect(result.executions[0].status).toBe("Success");
		expect(result.continuation_token).toBe("server-token");
	});

	it("does not insert an unseen row into a filtered page", () => {
		const page: ExecutionHistoryPage = {
			executions: [row("a", "2026-08-27T18:00:00Z")],
			continuation_token: null,
		};

		expect(
			mergeExecutionHistoryPage(
				page,
				update("new", "2026-08-27T18:01:00Z"),
				false,
			),
		).toBe(page);
	});

	it("prepends without trimming when the server says there is no next page", () => {
		const page: ExecutionHistoryPage = {
			executions: [row("a", "2026-08-27T18:00:00Z")],
			continuation_token: null,
		};

		const result = mergeExecutionHistoryPage(
			page,
			update("new", "2026-08-27T18:01:00Z"),
			true,
		);

		expect(result.executions).toHaveLength(2);
		expect(result.continuation_token).toBeNull();
	});
});

