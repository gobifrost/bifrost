import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import {
	getKubernetesExecution,
	getKubernetesStatus,
	updateKubernetesExecution,
} from "./kubernetes";

describe("kubernetes service", () => {
	beforeEach(() => authFetchMock.mockReset());

	it("loads status and execution settings", async () => {
		authFetchMock
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({ configured: true, backend: "kubernetes" }),
					{ status: 200 },
				),
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						job_types: [
							{
								job_type: "application.deploy",
								title: "App deploys",
								description: "Compile apps in pods.",
								enabled: true,
								default_enabled: true,
								allowed_by_deployment: true,
							},
						],
					}),
					{ status: 200 },
				),
			);

		await expect(getKubernetesStatus()).resolves.toEqual({
			configured: true,
			backend: "kubernetes",
		});
		await expect(getKubernetesExecution()).resolves.toMatchObject({
			job_types: [
				expect.objectContaining({
					job_type: "application.deploy",
					enabled: true,
				}),
			],
		});
	});

	it("toggles one job type and surfaces server errors", async () => {
		authFetchMock.mockResolvedValueOnce(
			new Response(JSON.stringify({ job_types: [] }), { status: 200 }),
		);

		await expect(
			updateKubernetesExecution("application.deploy", { enabled: false }),
		).resolves.toEqual({ job_types: [] });
		expect(authFetchMock).toHaveBeenCalledWith(
			"/api/admin/kubernetes/execution",
			expect.objectContaining({ method: "PUT" }),
		);

		authFetchMock.mockResolvedValueOnce(
			new Response(JSON.stringify({ detail: "nope" }), { status: 422 }),
		);
		await expect(
			updateKubernetesExecution("application.deploy", { enabled: true }),
		).rejects.toThrow("nope");
	});

	it("sends an explicit null to clear a concurrency override", async () => {
		authFetchMock.mockResolvedValueOnce(
			new Response(JSON.stringify({ job_types: [] }), { status: 200 }),
		);

		await updateKubernetesExecution("application.deploy", {
			enabled: true,
			maxConcurrency: null,
		});
		const [, init] = authFetchMock.mock.calls[0] as [
			string,
			RequestInit,
		];
		expect(JSON.parse(init.body as string)).toMatchObject({
			job_type: "application.deploy",
			max_concurrency: null,
		});
	});
});
