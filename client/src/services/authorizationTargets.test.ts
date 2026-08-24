import { beforeEach, describe, expect, it, vi } from "vitest";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: get },
}));

import { getAuthorizationTargets } from "./authorizationTargets";

describe("getAuthorizationTargets", () => {
	beforeEach(() => get.mockReset());

	it("returns the server-discovered request contexts", async () => {
		const response = {
			targets: [
				{
					boundary: "organization:customer-one",
					kind: "organization" as const,
					label: "Customer One",
					capabilities: ["users.read"],
					organization_id: "customer-one",
					is_provider: false,
				},
			],
		};
		get.mockResolvedValue({ data: response });

		await expect(getAuthorizationTargets()).resolves.toEqual(response);
		expect(get).toHaveBeenCalledWith("/auth/authorization-targets");
	});

	it("surfaces discovery failures", async () => {
		get.mockResolvedValue({ error: { detail: "Forbidden" } });

		await expect(getAuthorizationTargets()).rejects.toThrow(
			"Failed to load authorization contexts",
		);
	});
});
