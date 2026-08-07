import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: (...args: unknown[]) => mockGet(...args) },
}));

import {
	getSchedulerDiagnostics,
	getSchedulerTaskHistory,
} from "./schedulerDiagnostics";

describe("scheduler diagnostics service", () => {
	beforeEach(() => mockGet.mockReset());

	it("loads a scheduler snapshot", async () => {
		const snapshot = { generated_at: "2026-08-05T22:00:00Z" };
		mockGet.mockResolvedValue({ data: snapshot });

		await expect(getSchedulerDiagnostics()).resolves.toBe(snapshot);
		expect(mockGet).toHaveBeenCalledWith("/api/platform/scheduler", {
			signal: undefined,
		});
	});

	it("loads recent runs for one scheduled task", async () => {
		const history = { task_id: "oauth_token_refresh", runs: [] };
		mockGet.mockResolvedValue({ data: history });

		await expect(
			getSchedulerTaskHistory("oauth_token_refresh", { limit: 5 }),
		).resolves.toBe(history);
		expect(mockGet).toHaveBeenCalledWith(
			"/api/platform/scheduler/tasks/{task_id}/runs",
			{
				params: {
					path: { task_id: "oauth_token_refresh" },
					query: { limit: 5 },
				},
				signal: undefined,
			},
		);
	});

	it("surfaces API failures", async () => {
		mockGet.mockResolvedValue({ error: { detail: "forbidden" } });
		await expect(getSchedulerDiagnostics()).rejects.toThrow(
			"Failed to load scheduler diagnostics",
		);
	});
});
