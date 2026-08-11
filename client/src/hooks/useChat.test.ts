import { beforeEach, describe, expect, it, vi } from "vitest";

const { useInfiniteQuery } = vi.hoisted(() => ({
	useInfiniteQuery: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useInfiniteQuery,
		useMutation: vi.fn(),
		useQuery: vi.fn(),
	},
	apiClient: {
		DELETE: vi.fn(),
		GET: vi.fn(),
		POST: vi.fn(),
	},
}));

import { useMessages } from "./useChat";

describe("useMessages", () => {
	beforeEach(() => {
		useInfiniteQuery.mockReset();
	});

	it("keeps the initial history cursor within the database sequence range", () => {
		useMessages("conversation-1");

		expect(useInfiniteQuery).toHaveBeenCalledWith(
			"get",
			"/api/chat/conversations/{conversation_id}/messages",
			expect.objectContaining({
				params: {
					path: { conversation_id: "conversation-1" },
					query: { limit: 100 },
				},
			}),
			expect.objectContaining({
				initialPageParam: 2_147_483_647,
				pageParamName: "before_sequence",
			}),
		);
	});
});
