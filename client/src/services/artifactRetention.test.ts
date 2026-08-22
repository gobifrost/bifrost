import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import {
	cleanupExpiredArtifacts,
	getArtifactRetentionSettings,
	updateArtifactRetentionSettings,
} from "./artifactRetention";

describe("artifact retention service", () => {
	beforeEach(() => authFetchMock.mockReset());

	it("loads and updates retention settings", async () => {
		authFetchMock
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({ enabled: false, retention_days: 90 }),
					{
						status: 200,
					},
				),
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({ enabled: true, retention_days: 30 }),
					{
						status: 200,
					},
				),
			);

		await expect(getArtifactRetentionSettings()).resolves.toEqual({
			enabled: false,
			retention_days: 90,
		});
		await updateArtifactRetentionSettings({
			enabled: true,
			retention_days: 30,
		});

		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/artifact-retention/settings",
			expect.objectContaining({
				method: "PUT",
				body: '{"enabled":true,"retention_days":30}',
			}),
		);
	});

	it("runs manual cleanup and surfaces API errors", async () => {
		authFetchMock.mockResolvedValueOnce(
			new Response(
				JSON.stringify({
					job_id: "job-1",
					status: "queued",
					reused: false,
					notification_id: "notification-1",
				}),
				{ status: 202 },
			),
		);
		await expect(cleanupExpiredArtifacts()).resolves.toMatchObject({
			job_id: "job-1",
			status: "queued",
		});

		authFetchMock.mockResolvedValueOnce(
			new Response(JSON.stringify({ detail: "Retention failed" }), {
				status: 500,
			}),
		);
		await expect(cleanupExpiredArtifacts()).rejects.toThrow(
			"Retention failed",
		);
	});
});
