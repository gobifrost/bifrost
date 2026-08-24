import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: (...args: unknown[]) => mockGet(...args),
		POST: (...args: unknown[]) => mockPost(...args),
	},
}));

import { cancelPlatformJob, getPlatformJobs } from "./platformJobs";

describe("platform jobs service", () => {
	beforeEach(() => {
		mockGet.mockReset();
		mockPost.mockReset();
	});

	it("lists platform jobs with explicit history options", async () => {
		const jobs = [{ id: "job-1", status: "running" }];
		const response = { jobs, total: 1, limit: 25, offset: 25 };
		mockGet.mockResolvedValue({ data: response });

		await expect(
			getPlatformJobs({
				activeOnly: false,
				limit: 25,
				offset: 25,
				status: "running",
				search: "deploy",
			}),
		).resolves.toBe(response);
		expect(mockGet).toHaveBeenCalledWith("/api/platform-jobs", {
			params: {
				query: {
					active_only: false,
					limit: 25,
					offset: 25,
					status: "running",
					search: "deploy",
				},
			},
			signal: undefined,
		});
	});

	it("cancels through the shared platform-job endpoint", async () => {
		const result = {
			accepted: true,
			job: { id: "job-1", status: "cancelled" },
		};
		mockPost.mockResolvedValue({ data: result });

		await expect(cancelPlatformJob("job-1")).resolves.toBe(result);
		expect(mockPost).toHaveBeenCalledWith(
			"/api/platform-jobs/{job_id}/cancel",
			{ params: { path: { job_id: "job-1" } } },
		);
	});

	it("surfaces list and cancellation failures", async () => {
		mockGet.mockResolvedValue({ error: { detail: "forbidden" } });
		mockPost.mockResolvedValue({ error: { detail: "conflict" } });

		await expect(getPlatformJobs()).rejects.toThrow(
			"Failed to load platform jobs",
		);
		await expect(cancelPlatformJob("job-1")).rejects.toThrow(
			"Failed to cancel platform job",
		);
	});
});
