import { beforeEach, describe, expect, it, vi } from "vitest";

const mockUseInfiniteQuery = vi.fn();

vi.mock("@tanstack/react-query", () => ({
	useInfiniteQuery: (...args: unknown[]) => mockUseInfiniteQuery(...args),
	useQuery: vi.fn(),
	useQueryClient: vi.fn(),
}));

import { useInfiniteAgentRuns } from "./agentRuns";

describe("useInfiniteAgentRuns", () => {
	beforeEach(() => {
		mockUseInfiniteQuery.mockReset();
	});

	it("does not request runs when the caller disables the query", () => {
		useInfiniteAgentRuns({ agentId: "agent-1", enabled: false });

		expect(mockUseInfiniteQuery).toHaveBeenCalledWith(
			expect.objectContaining({ enabled: false }),
		);
	});
});
