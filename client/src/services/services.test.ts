import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: (...args: unknown[]) => get(...args),
		POST: (...args: unknown[]) => post(...args),
		PATCH: (...args: unknown[]) => patch(...args),
	},
	$api: { useQuery: vi.fn() },
}));

import {
	disableService,
	enableService,
	getService,
	listServiceAttempts,
	listServiceLogs,
	listServices,
	restartService,
	startService,
	stopService,
	updateServicePolicy,
} from "./services";

describe("services client", () => {
	beforeEach(() => {
		get.mockReset();
		post.mockReset();
		patch.mockReset();
	});

	it("lists services with pagination", async () => {
		get.mockResolvedValue({
			data: { items: [{ id: "svc-1" }], total: 1 },
			error: undefined,
		});
		await expect(listServices(50, 10)).resolves.toEqual({
			items: [{ id: "svc-1" }],
			total: 1,
		});
		expect(get).toHaveBeenCalledWith("/api/services", {
			params: { query: { limit: 50, offset: 10 } },
		});
	});

	it("loads a single service", async () => {
		get.mockResolvedValue({
			data: { id: "svc-1" },
			error: undefined,
		});
		await expect(getService("svc-1")).resolves.toEqual({ id: "svc-1" });
		expect(get).toHaveBeenCalledWith("/api/services/{service_id}", {
			params: { path: { service_id: "svc-1" } },
		});
	});

	it("updates policy with the caller's fields", async () => {
		patch.mockResolvedValue({
			data: { id: "svc-1", restart_policy: "never" },
			error: undefined,
		});
		await expect(
			updateServicePolicy("svc-1", { restart_policy: "never" }),
		).resolves.toEqual({ id: "svc-1", restart_policy: "never" });
		expect(patch).toHaveBeenCalledWith("/api/services/{service_id}", {
			params: { path: { service_id: "svc-1" } },
			body: { restart_policy: "never" },
		});
	});

	it.each([
		["start", startService, "/api/services/{service_id}/start"],
		["stop", stopService, "/api/services/{service_id}/stop"],
		["restart", restartService, "/api/services/{service_id}/restart"],
		["enable", enableService, "/api/services/{service_id}/enable"],
		["disable", disableService, "/api/services/{service_id}/disable"],
	] as const)(
		"posts the %s action to its endpoint",
		async (_action, fn, path) => {
			post.mockResolvedValue({
				data: { id: "svc-1" },
				error: undefined,
			});
			await expect(fn("svc-1")).resolves.toEqual({ id: "svc-1" });
			expect(post).toHaveBeenCalledWith(path, {
				params: { path: { service_id: "svc-1" } },
			});
		},
	);

	it("lists attempts for a service", async () => {
		get.mockResolvedValue({
			data: { items: [{ id: "att-1" }], total: 1 },
			error: undefined,
		});
		await expect(listServiceAttempts("svc-1", 25, 0)).resolves.toEqual({
			items: [{ id: "att-1" }],
			total: 1,
		});
		expect(get).toHaveBeenCalledWith(
			"/api/services/{service_id}/attempts",
			{
				params: {
					path: { service_id: "svc-1" },
					query: { limit: 25, offset: 0 },
				},
			},
		);
	});

	it("lists logs with filters and a 200 default limit", async () => {
		get.mockResolvedValue({
			data: { items: [{ id: 1 }], total: 1 },
			error: undefined,
		});
		await expect(
			listServiceLogs("svc-1", {
				levels: ["INFO", "ERROR"],
				startDate: "2026-09-20T00:00:00.000Z",
			}),
		).resolves.toEqual({ items: [{ id: 1 }], total: 1 });
		expect(get).toHaveBeenCalledWith("/api/services/{service_id}/logs", {
			params: {
				path: { service_id: "svc-1" },
				query: {
					attempt_id: undefined,
					levels: ["INFO", "ERROR"],
					start_date: "2026-09-20T00:00:00.000Z",
					end_date: undefined,
					limit: 200,
					continuation_token: undefined,
					order: "chronological",
				},
			},
		});
	});

	it("pages older logs newest-first with a continuation token", async () => {
		get.mockResolvedValue({
			data: { items: [{ id: 3 }], total: 3, continuation_token: null },
			error: undefined,
		});
		await expect(
			listServiceLogs("svc-1", {
				limit: 2,
				continuationToken: "abc123",
				order: "newest_first",
			}),
		).resolves.toEqual({
			items: [{ id: 3 }],
			total: 3,
			continuation_token: null,
		});
		expect(get).toHaveBeenCalledWith("/api/services/{service_id}/logs", {
			params: {
				path: { service_id: "svc-1" },
				query: {
					attempt_id: undefined,
					levels: undefined,
					start_date: undefined,
					end_date: undefined,
					limit: 2,
					continuation_token: "abc123",
					order: "newest_first",
				},
			},
		});
	});

	it("raises a friendly error when log listing fails", async () => {
		get.mockResolvedValue({ data: undefined, error: true });
		await expect(listServiceLogs("svc-1")).rejects.toThrow(
			"Could not load service logs.",
		);
	});

	it("raises a friendly error when listing fails", async () => {
		get.mockResolvedValue({ data: undefined, error: true });
		await expect(listServices()).rejects.toThrow(
			"Could not load services.",
		);
	});

	it("raises an action-specific error when an action fails", async () => {
		post.mockResolvedValue({ data: undefined, error: true });
		await expect(stopService("svc-1")).rejects.toThrow(
			"Could not stop the service.",
		);
	});
});
