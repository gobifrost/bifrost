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
});
