import { beforeEach, describe, expect, it, vi } from "vitest";

const mockUseQuery = vi.fn();

vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: (...args: unknown[]) => mockUseQuery(...args) },
	apiClient: {},
}));

import { useExecutions } from "./useExecutions";

describe("useExecutions", () => {
	beforeEach(() => {
		mockUseQuery.mockReset();
	});

	it("preserves previous page data only for same-scope, same-filter continuation changes", () => {
		const previousData = {
			executions: [{ execution_id: "page-one" }],
			continuation_token: "next",
		};
		const previousQuery = {
			queryKey: [
				"get",
				"/api/executions",
				{
					params: {
						query: {
							scope: "org-1",
							status: "Running",
							continuationToken: "first",
						},
					},
				},
			],
		};

		useExecutions("org-1", { status: "Running" }, "second", {
			preservePageData: true,
		});

		const options = mockUseQuery.mock.calls[0][3];
		expect(options.placeholderData(previousData, previousQuery)).toBe(
			previousData,
		);

		useExecutions("org-2", { status: "Running" }, "second", {
			preservePageData: true,
		});

		expect(
			mockUseQuery.mock.calls[1][3].placeholderData(
				previousData,
				previousQuery,
			),
		).toBeUndefined();

		useExecutions("org-1", { status: "Failed" }, "second", {
			preservePageData: true,
		});

		expect(
			mockUseQuery.mock.calls[2][3].placeholderData(
				previousData,
				previousQuery,
			),
		).toBeUndefined();
	});
});
