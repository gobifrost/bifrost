import { beforeEach, describe, expect, it, vi } from "vitest";

const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

import { listPromotionReviews, promoteSolution } from "./solutionPromotions";

beforeEach(() => {
	mockAuthFetch.mockReset();
});

describe("Solution promotion service", () => {
	it("returns the pending review queue", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () =>
				Promise.resolve({
					release_id: "release-1",
					published_solution_id: "published-1",
					promotions: [{ solution_id: "solution-1", name: "Ops app" }],
					total: 1,
				}),
		});

		const reviews = await listPromotionReviews();

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/solution-promotions", {
			signal: undefined,
		});
		expect(reviews[0].name).toBe("Ops app");
	});

	it("forwards the selected source-review boundary", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({ promotions: [], total: 0 }),
		});

		await listPromotionReviews({ boundary: "managed_organizations" });

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/solution-promotions", {
			signal: undefined,
			headers: { "X-Bifrost-Boundary": "managed_organizations" },
		});
	});

	it("submits the exact administrator approvals", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () =>
				Promise.resolve({
					solution_id: "solution-1",
					target: "company",
					visibility: "shared",
					promoted_revision_id: "revision-1",
					roles_created: ["Dispatcher"],
				}),
		});
		const request = {
			target: "company" as const,
			runtime_mode: "isolated" as const,
			approve_role_creation: true,
			approved_connection_names: ["HaloPSA"],
			allow_global_repo_access: false,
			role_user_assignments: { Dispatcher: ["user-1"] },
		};

		const result = await promoteSolution("solution-1", request);

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/solution-promotions/solution-1/promote",
			{
				method: "POST",
				body: JSON.stringify(request),
				signal: undefined,
			},
		);
		expect(result.roles_created).toEqual(["Dispatcher"]);
	});

	it("keeps source review and destination selection separate", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({ solution_id: "solution-1" }),
		});
		const request = {
			target: "global" as const,
			runtime_mode: "isolated" as const,
			approve_role_creation: false,
			approved_connection_names: [],
			allow_global_repo_access: false,
			role_user_assignments: {},
		};

		await promoteSolution("solution-1", request, {
			sourceBoundary: "organization:org-1",
		});

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/solution-promotions/solution-1/promote",
			expect.objectContaining({
				headers: { "X-Bifrost-Boundary": "organization:org-1" },
				body: JSON.stringify(request),
			}),
		);
	});
});
