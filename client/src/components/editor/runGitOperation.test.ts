import { describe, expect, it, vi } from "vitest";

import type { PlatformJobUpdate } from "@/services/websocket";

type JobCallback = (job: PlatformJobUpdate) => void;

const mocks = vi.hoisted(() => ({
	callbacks: new Map<string, JobCallback>(),
	connect: vi.fn().mockResolvedValue(undefined),
	getPlatformJob: vi.fn(),
}));

vi.mock("@/lib/uuid", () => ({ generateUUID: () => "requested-job" }));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connect: mocks.connect,
		onPlatformJobUpdate: vi.fn((jobId: string, callback: JobCallback) => {
			mocks.callbacks.set(jobId, callback);
			return vi.fn();
		}),
	},
}));
vi.mock("@/services/platformJobs", () => ({
	getPlatformJob: mocks.getPlatformJob,
}));

import { runGitOp } from "./runGitOperation";

function job(overrides: Partial<PlatformJobUpdate> = {}): PlatformJobUpdate {
	return {
		id: "requested-job",
		job_type: "workspace.git",
		payload_version: 1,
		resource_type: "workspace",
		resource_id: "git",
		resource_lock_key: "workspace",
		priority: 500,
		title: "Fetch",
		execution_backend: "local",
		requested_by_user_id: "user-1",
		requested_by_name: "Dev",
		status: "running",
		progress: { phase: "Fetching", current: 1, total: 2, percent: 50 },
		revision: 1,
		attempt: 1,
		max_attempts: 2,
		can_cancel: false,
		created_at: "2026-09-21T12:00:00Z",
		updated_at: "2026-09-21T12:00:00Z",
		...overrides,
	};
}

describe("runGitOp", () => {
	it("subscribes before queueing and resolves a terminal platform-job update", async () => {
		mocks.callbacks.clear();
		mocks.getPlatformJob.mockResolvedValue(job());
		const queue = vi.fn(async (jobId: string) => {
			expect(mocks.callbacks.has(jobId)).toBe(true);
			return { job_id: jobId, status: "queued" };
		});

		const resultPromise = runGitOp<{ success: boolean }>(queue, "fetch");
		await vi.waitFor(() => expect(queue).toHaveBeenCalledWith("requested-job"));
		mocks.callbacks.get("requested-job")?.(
			job({ status: "succeeded", result: { success: true } }),
		);

		await expect(resultPromise).resolves.toEqual({ success: true });
		expect(mocks.getPlatformJob).toHaveBeenCalledWith("requested-job");
	});

	it("uses the status snapshot when a fast job finishes before its update arrives", async () => {
		mocks.callbacks.clear();
		mocks.getPlatformJob.mockResolvedValue(
			job({ status: "succeeded", result: { success: true } }),
		);

		await expect(
			runGitOp(async () => ({ job_id: "requested-job", status: "queued" }), "status"),
		).resolves.toEqual({ success: true });
	});
});
