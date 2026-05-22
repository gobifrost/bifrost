import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: (...args: unknown[]) => mockGet(...args),
		POST: (...args: unknown[]) => mockPost(...args),
		PATCH: (...args: unknown[]) => mockPatch(...args),
		DELETE: (...args: unknown[]) => mockDelete(...args),
	},
}));

import {
	createClaim,
	deleteClaim,
	getClaim,
	listClaims,
	updateClaim,
} from "./claims";

beforeEach(() => {
	mockGet.mockReset();
	mockPost.mockReset();
	mockPatch.mockReset();
	mockDelete.mockReset();
});

describe("claims service", () => {
	it("lists claims", async () => {
		mockGet.mockResolvedValue({ data: { claims: [] } });

		const out = await listClaims();

		expect(mockGet).toHaveBeenCalledWith("/api/claims", {});
		expect(out).toEqual({ claims: [] });
	});

	it("gets a claim by name", async () => {
		mockGet.mockResolvedValue({
			data: { name: "allowed_campus_ids", type: "list", query: {} },
		});

		const out = await getClaim("allowed_campus_ids");

		expect(mockGet).toHaveBeenCalledWith("/api/claims/{name}", {
			params: { path: { name: "allowed_campus_ids" } },
		});
		expect(out.name).toBe("allowed_campus_ids");
	});

	it("creates a claim with the body", async () => {
		const body = {
			name: "allowed_campus_ids",
			type: "list" as const,
			query: { table: "user_campus_access", select: "campus_id" },
		};
		mockPost.mockResolvedValue({ data: body });

		await createClaim(body);

		expect(mockPost).toHaveBeenCalledWith("/api/claims", { body });
	});

	it("updates a claim by name", async () => {
		mockPatch.mockResolvedValue({
			data: {
				name: "allowed_campus_ids",
				type: "list",
				description: "Campus access",
				query: {},
			},
		});

		await updateClaim("allowed_campus_ids", { description: "Campus access" });

		expect(mockPatch).toHaveBeenCalledWith("/api/claims/{name}", {
			params: { path: { name: "allowed_campus_ids" } },
			body: { description: "Campus access" },
		});
	});

	it("deletes a claim by name", async () => {
		mockDelete.mockResolvedValue({ data: undefined });

		await deleteClaim("allowed_campus_ids");

		expect(mockDelete).toHaveBeenCalledWith("/api/claims/{name}", {
			params: { path: { name: "allowed_campus_ids" } },
		});
	});

	it("forwards an AbortSignal", async () => {
		mockGet.mockResolvedValue({ data: { claims: [] } });
		const controller = new AbortController();

		await listClaims({ signal: controller.signal });

		expect(mockGet).toHaveBeenCalledWith("/api/claims", {
			signal: controller.signal,
		});
	});

	it("throws on API errors", async () => {
		mockGet.mockResolvedValue({ error: { detail: "boom" } });

		await expect(listClaims()).rejects.toThrow(/boom/);
	});
});
