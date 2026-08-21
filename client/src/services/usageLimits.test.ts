import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: vi.fn(),
		PUT: vi.fn(),
		DELETE: vi.fn(),
	},
}));

import { apiClient } from "@/lib/api-client";
import {
	deleteUsageLimit,
	getEffectiveUsageLimits,
	listUsageLimits,
	saveUsageLimit,
} from "./usageLimits";

const mockGet = apiClient.GET as unknown as ReturnType<typeof vi.fn>;
const mockPut = apiClient.PUT as unknown as ReturnType<typeof vi.fn>;
const mockDelete = apiClient.DELETE as unknown as ReturnType<typeof vi.fn>;

describe("usageLimits service", () => {
	beforeEach(() => {
		mockGet.mockReset();
		mockPut.mockReset();
		mockDelete.mockReset();
	});

	it("lists policies with the selected boundary header", async () => {
		mockGet.mockResolvedValue({ data: { policies: [] }, error: undefined });

		await expect(
			listUsageLimits({ boundary: "organization:org-1" }),
		).resolves.toEqual({ policies: [] });

		expect(mockGet).toHaveBeenCalledWith("/api/settings/ai/usage-limits", {
			headers: { "X-Bifrost-Boundary": "organization:org-1" },
		});
	});

	it("reads an effective target through the generated path", async () => {
		mockGet.mockResolvedValue({
			data: { subject_scope: "organization", aggregate: [] },
			error: undefined,
		});

		await getEffectiveUsageLimits(
			{ scope: "organization", targetId: "org-1" },
			{ boundary: "organization:org-1" },
		);

		expect(mockGet).toHaveBeenCalledWith(
			"/api/settings/ai/usage-limits/effective/{scope}/{target_id}",
			{
				headers: { "X-Bifrost-Boundary": "organization:org-1" },
				params: {
					path: { scope: "organization", target_id: "org-1" },
				},
			},
		);
	});

	it("saves and deletes a policy", async () => {
		mockPut.mockResolvedValue({
			data: { id: 1, scope: "platform", scope_key: "platform" },
			error: undefined,
		});
		mockDelete.mockResolvedValue({ error: undefined });

		await saveUsageLimit(
			{ scope: "platform", targetId: "platform" },
			{
				aggregate_period: "monthly",
				per_run: { model_requests: 4 },
			},
			{ boundary: "platform" },
		);
		await deleteUsageLimit(
			{ scope: "platform", targetId: "platform" },
			{ boundary: "platform" },
		);

		expect(mockPut).toHaveBeenCalledWith(
			"/api/settings/ai/usage-limits/{scope}/{target_id}",
			expect.objectContaining({
				headers: { "X-Bifrost-Boundary": "platform" },
				params: { path: { scope: "platform", target_id: "platform" } },
				body: {
					aggregate_period: "monthly",
					per_run: { model_requests: 4 },
				},
			}),
		);
		expect(mockDelete).toHaveBeenCalledWith(
			"/api/settings/ai/usage-limits/{scope}/{target_id}",
			expect.objectContaining({
				headers: { "X-Bifrost-Boundary": "platform" },
			}),
		);
	});
});
