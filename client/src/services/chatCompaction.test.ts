import { beforeEach, describe, expect, it, vi } from "vitest";

import { compactConversation } from "./chatCompaction";

const postMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
	apiClient: {
		POST: (...args: unknown[]) => postMock(...args),
	},
}));

describe("compactConversation", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("POSTs to the conversation compact endpoint with the path param", async () => {
		postMock.mockResolvedValue({
			data: {
				compacted: true,
				turns_compacted: 3,
				tokens_before: 10_000,
				tokens_after: 1_000,
				message: "Compacted 3 earlier turns into a summary.",
			},
			error: undefined,
		});

		const result = await compactConversation("conv-123");

		expect(postMock).toHaveBeenCalledWith(
			"/api/chat/conversations/{conversation_id}/compact",
			{ params: { path: { conversation_id: "conv-123" } } },
		);
		expect(result.compacted).toBe(true);
		expect(result.turns_compacted).toBe(3);
	});

	it("throws when the API returns an error", async () => {
		postMock.mockResolvedValue({ data: undefined, error: { detail: "nope" } });
		await expect(compactConversation("conv-123")).rejects.toThrow(
			/Compaction failed/,
		);
	});
});
