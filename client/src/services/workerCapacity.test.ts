import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() },
}));

import { apiClient } from "@/lib/api-client";
import {
	connectCloudflareCapacity,
	getCloudflareCapacity,
	updateCloudflareCapacityRules,
} from "./workerCapacity";

const mockGet = apiClient.GET as unknown as ReturnType<typeof vi.fn>;
const mockPost = apiClient.POST as unknown as ReturnType<typeof vi.fn>;
const mockPut = apiClient.PUT as unknown as ReturnType<typeof vi.fn>;

describe("worker capacity service", () => {
	beforeEach(() => {
		mockGet.mockReset();
		mockPost.mockReset();
		mockPut.mockReset();
	});

	it("loads redacted Cloudflare capacity status", async () => {
		const status = {
			configured: true,
			active: true,
			status: "ready" as const,
			accounts: [],
			max_workers: 4,
			memory_limit_bytes: 4 * 1024 ** 3,
		};
		mockGet.mockResolvedValue({ data: status, error: undefined });

		await expect(getCloudflareCapacity()).resolves.toEqual(status);
		expect(mockGet).toHaveBeenCalledWith(
			"/api/admin/worker-capacity/cloudflare",
		);
	});

	it("connects without resending a stored API key", async () => {
		const accepted = {
			job_id: "job-1",
			connection: {
				configured: true,
				active: false,
				status: "provisioning" as const,
				accounts: [],
			},
		};
		mockPost.mockResolvedValue({ data: accepted, error: undefined });

		await expect(
			connectCloudflareCapacity({
				account_id: "a".repeat(32),
				max_workers: 5,
			}),
		).resolves.toEqual(accepted);
		expect(mockPost).toHaveBeenCalledWith(
			"/api/admin/worker-capacity/cloudflare",
			{
				body: { account_id: "a".repeat(32), max_workers: 5 },
			},
		);
	});

	it("surfaces the server detail or message when a request is refused", async () => {
		mockGet.mockResolvedValue({
			data: undefined,
			error: { detail: "Only platform admins can manage workers" },
		});
		mockPost.mockResolvedValue({
			data: undefined,
			error: { message: "A Cloudflare API token is required" },
		});

		await expect(getCloudflareCapacity()).rejects.toThrow(
			"Only platform admins can manage workers",
		);
		await expect(connectCloudflareCapacity({ max_workers: 2 })).rejects.toThrow(
			"A Cloudflare API token is required",
		);
	});

	it("saves Cloudflare start rules without sending credentials", async () => {
		const status = {
			configured: true,
			active: true,
			status: "ready" as const,
			accounts: [],
			rules: {
				workflows: true,
				agents: false,
				app_builds: true,
				other_jobs: true,
				prefer_kubernetes: true,
			},
		};
		mockPut.mockResolvedValue({ data: status, error: undefined });

		await expect(updateCloudflareCapacityRules(status.rules)).resolves.toEqual(
			status,
		);
		expect(mockPut).toHaveBeenCalledWith(
			"/api/admin/worker-capacity/cloudflare/rules",
			{ body: status.rules },
		);
	});
});
