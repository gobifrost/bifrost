import { beforeEach, describe, expect, it, vi } from "vitest";

const mockUseQuery = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => mockUseQuery(...args),
	},
}));

vi.mock("@tanstack/react-query", () => ({
	useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

import { useDynamicValues } from "./events";

describe("event dynamic values service", () => {
	beforeEach(() => {
		mockUseQuery.mockReset();
		mockUseQuery.mockReturnValue({ data: undefined });
	});

	it("sends the selected organization with the integration context", () => {
		useDynamicValues(
			"microsoft_graph",
			"list_users",
			"integration-1",
			"organization-1",
			{ user_id: "user-1" },
		);

		expect(mockUseQuery).toHaveBeenCalledWith(
			"post",
			"/api/events/adapters/{adapter_name}/dynamic-values",
			{
				params: { path: { adapter_name: "microsoft_graph" } },
				body: {
					operation: "list_users",
					integration_id: "integration-1",
					organization_id: "organization-1",
					current_config: { user_id: "user-1" },
				},
			},
			{ enabled: true, staleTime: 5 * 60 * 1000 },
		);
	});
});
