import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import {
	KubernetesRequestError,
	getKubernetesExecution,
	getKubernetesStatus,
	updateKubernetesExecution,
	updateKubernetesPolicy,
} from "./kubernetes";

describe("kubernetes service", () => {
	beforeEach(() => authFetchMock.mockReset());

	it("loads status and execution settings", async () => {
		authFetchMock
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ configured: true }), {
					status: 200,
				}),
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						job_types: [
							{
								job_type: "application.deploy",
								title: "App Deploys",
								description: "Compiles apps in an isolated pod.",
								enabled: true,
								default_enabled: true,
							},
						],
					}),
					{ status: 200 },
				),
			);

		await expect(getKubernetesStatus()).resolves.toEqual({
			configured: true,
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
		const [, init] = authFetchMock.mock.calls[0] as [string, RequestInit];
		expect(JSON.parse(init.body as string)).toMatchObject({
			job_type: "application.deploy",
			max_concurrency: null,
		});
	});

	it("sends only the changed policy fields", async () => {
		authFetchMock.mockResolvedValueOnce(
			new Response(JSON.stringify({ job_types: [] }), { status: 200 }),
		);

		await updateKubernetesPolicy({ max_workers: 10 });

		const [url, init] = authFetchMock.mock.calls[0] as [
			string,
			RequestInit,
		];
		expect(url).toBe("/api/admin/kubernetes/policy");
		expect(init.method).toBe("PUT");
		expect(JSON.parse(init.body as string)).toEqual({ max_workers: 10 });
	});

	it("reports which field a validation error is about", async () => {
		authFetchMock.mockResolvedValueOnce(
			new Response(
				JSON.stringify({
					detail: [
						{
							type: "less_than_equal",
							loc: ["body", "max_workers"],
							msg: "Input should be less than or equal to 100",
						},
					],
				}),
				{ status: 422 },
			),
		);

		const error = await updateKubernetesPolicy({ max_workers: 500 }).catch(
			(caught: unknown) => caught,
		);
		expect(error).toBeInstanceOf(KubernetesRequestError);
		expect(error).toMatchObject({
			message: "Input should be less than or equal to 100",
			status: 422,
			field: "max_workers",
		});
	});

	it("keeps a plain refusal message without a field", async () => {
		authFetchMock.mockResolvedValueOnce(
			new Response(
				JSON.stringify({
					detail: "Warm workers must not exceed max workers",
				}),
				{ status: 422 },
			),
		);

		await expect(
			updateKubernetesPolicy({ warm_workers: 9 }),
		).rejects.toMatchObject({
			message: "Warm workers must not exceed max workers",
			status: 422,
			field: undefined,
		});
	});
});
