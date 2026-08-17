import { beforeEach, describe, expect, it, vi } from "vitest";

const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

import {
	cancelPlatformJob,
	getPlatformJob,
	listPlatformJobs,
} from "./platformJobs";

function response(body: unknown, status = 200) {
	return {
		ok: status >= 200 && status < 300,
		status,
		json: () => Promise.resolve(body),
	};
}

beforeEach(() => mockAuthFetch.mockReset());

describe("platform job service", () => {
	it("loads the durable job snapshot", async () => {
		mockAuthFetch.mockResolvedValue(
			response({ id: "job-1", status: "waiting" }),
		);

		const job = await getPlatformJob("job-1");

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/platform-jobs/job-1", {
			signal: undefined,
		});
		expect(job.status).toBe("waiting");
	});

	it("requests cancellation through the shared job endpoint", async () => {
		mockAuthFetch.mockResolvedValue(
			response({
				job: { id: "job-1", status: "cancel_requested" },
				accepted: true,
			}),
		);

		const cancelled = await cancelPlatformJob("job-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/platform-jobs/job-1/cancel",
			{ method: "POST" },
		);
		expect(cancelled.job.status).toBe("cancel_requested");
	});

	it("lists completed jobs for diagnostics", async () => {
		mockAuthFetch.mockResolvedValue(
			response({ jobs: [{ id: "job-1", status: "succeeded" }] }),
		);

		const jobs = await listPlatformJobs({ activeOnly: false, limit: 100 });

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/platform-jobs?active_only=false&limit=100",
			{ signal: undefined },
		);
		expect(jobs).toHaveLength(1);
	});

	it("preserves the server's recovery guidance", async () => {
		mockAuthFetch.mockResolvedValue(
			response(
				{ detail: "The runner has already finished this build" },
				409,
			),
		);

		await expect(cancelPlatformJob("job-1")).rejects.toThrow(
			"The runner has already finished this build",
		);
	});
});
