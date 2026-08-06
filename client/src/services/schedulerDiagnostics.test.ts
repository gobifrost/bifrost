import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: (...args: unknown[]) => mockGet(...args) },
}));

import { getSchedulerDiagnostics } from "./schedulerDiagnostics";

describe("scheduler diagnostics service", () => {
	beforeEach(() => mockGet.mockReset());

	it("loads a bounded scheduler snapshot", async () => {
		const snapshot = { generated_at: "2026-08-05T22:00:00Z" };
		mockGet.mockResolvedValue({ data: snapshot });

		await expect(getSchedulerDiagnostics({ logLimit: 25 })).resolves.toBe(snapshot);
		expect(mockGet).toHaveBeenCalledWith("/api/platform/scheduler", {
			params: { query: { log_limit: 25 } },
			signal: undefined,
		});
	});

	it("surfaces API failures", async () => {
		mockGet.mockResolvedValue({ error: { detail: "forbidden" } });
		await expect(getSchedulerDiagnostics()).rejects.toThrow(
			"Failed to load scheduler diagnostics",
		);
	});
});
