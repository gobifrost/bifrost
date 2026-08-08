import { beforeEach, describe, expect, it, vi } from "vitest";

const mockPost = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: {},
	apiClient: {
		POST: (...args: unknown[]) => mockPost(...args),
	},
}));

import {
	createIsolatedApplicationLaunch,
	publishApplication,
} from "./useApplications";

beforeEach(() => {
	mockPost.mockReset();
});

describe("application publish enqueue", () => {
	it("returns the durable job and notification without browser polling", async () => {
		const operation = {
			job_id: "job-1",
			notification_id: "notification-1",
			status: "queued",
			reused: false,
		};
		mockPost.mockResolvedValue({ data: operation });

		const result = await publishApplication("app-1", "Ship it");

		expect(mockPost).toHaveBeenCalledOnce();
		expect(mockPost).toHaveBeenCalledWith(
			"/api/applications/{app_id}/publish",
			{
				params: { path: { app_id: "app-1" } },
				body: { message: "Ship it" },
			},
		);
		expect(result).toEqual(operation);
	});

	it("surfaces enqueue errors without pretending the publish completed", async () => {
		mockPost.mockResolvedValue({
			error: { detail: "Application is managed by a Solution" },
		});

		await expect(publishApplication("app-1")).rejects.toThrow(
			"Application is managed by a Solution",
		);
	});
});

describe("isolated application launch", () => {
	it("passes the visible app route to the one-time launch endpoint", async () => {
		mockPost.mockResolvedValue({
			data: { launch_url: "/api/builder-runtime/launch/once" },
		});

		const result = await createIsolatedApplicationLaunch(
			"app-1",
			"/reports?period=week#top",
		);

		expect(mockPost).toHaveBeenCalledWith(
			"/api/applications/{app_id}/isolated-launch",
			{
				params: {
					path: { app_id: "app-1" },
					query: { path: "/reports?period=week#top" },
				},
				signal: undefined,
			},
		);
		expect(result.launch_url).toContain("/api/builder-runtime/launch/");
	});
});
