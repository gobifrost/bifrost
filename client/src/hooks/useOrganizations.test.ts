import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockUseQuery } = vi.hoisted(() => ({
	mockUseQuery: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => mockUseQuery(...args),
	},
}));

import { useOrganizations } from "./useOrganizations";

beforeEach(() => {
	mockUseQuery.mockReset();
});

describe("useOrganizations", () => {
	it("requests active organizations by default", () => {
		useOrganizations();

		expect(mockUseQuery).toHaveBeenCalledWith(
			"get",
			"/api/organizations",
			{
				params: { query: { include_inactive: false } },
			},
			{ enabled: true },
		);
	});

	it("can request inactive organizations and preserve the enabled option", () => {
		useOrganizations({ includeInactive: true, enabled: false });

		expect(mockUseQuery).toHaveBeenCalledWith(
			"get",
			"/api/organizations",
			{
				params: { query: { include_inactive: true } },
			},
			{ enabled: false },
		);
	});
});
