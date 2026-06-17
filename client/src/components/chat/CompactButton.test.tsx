import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import userEvent from "@testing-library/user-event";

import { CompactButton, shouldSuggestCompaction } from "./CompactButton";
import type { components } from "@/lib/v1";

type MessagePublic = components["schemas"]["MessagePublic"];

const compactConversation = vi.fn();
vi.mock("@/services/chatCompaction", () => ({
	compactConversation: (id: string) => compactConversation(id),
}));

const toastSuccess = vi.fn();
const toastInfo = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...a: unknown[]) => toastSuccess(...a),
		info: (...a: unknown[]) => toastInfo(...a),
		error: (...a: unknown[]) => toastError(...a),
	},
}));

function assistant(tokens: number): MessagePublic {
	return {
		id: `m-${tokens}`,
		conversation_id: "c1",
		role: "assistant",
		content: "ok",
		token_count_input: tokens,
		sequence: 0,
		created_at: "2026-04-20T12:00:00Z",
	} as MessagePublic;
}

describe("shouldSuggestCompaction", () => {
	it("hidden below 70% of the window", () => {
		expect(shouldSuggestCompaction(60_000, 200_000)).toBe(false);
	});

	it("shown at or above 70%", () => {
		expect(shouldSuggestCompaction(140_000, 200_000)).toBe(true);
		expect(shouldSuggestCompaction(180_000, 200_000)).toBe(true);
	});

	it("hidden when usage is zero or window unknown", () => {
		expect(shouldSuggestCompaction(0, 200_000)).toBe(false);
		expect(shouldSuggestCompaction(140_000, null)).toBe(false);
	});
});

describe("CompactButton", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("renders nothing under the suggestion threshold", () => {
		const { container } = renderWithProviders(
			<CompactButton
				conversationId="c1"
				messages={[assistant(40_000)]}
				contextWindow={200_000}
			/>,
		);
		expect(container).toBeEmptyDOMElement();
	});

	it("compacts and toasts freed tokens on success", async () => {
		compactConversation.mockResolvedValue({
			compacted: true,
			turns_compacted: 5,
			tokens_before: 30_000,
			tokens_after: 2_000,
			message: "Compacted 5 earlier turns into a summary.",
		});
		const onCompacted = vi.fn();

		renderWithProviders(
			<CompactButton
				conversationId="c1"
				messages={[assistant(150_000)]}
				contextWindow={200_000}
				onCompacted={onCompacted}
			/>,
		);

		await userEvent.click(
			screen.getByRole("button", { name: /compact older turns/i }),
		);

		await waitFor(() => expect(compactConversation).toHaveBeenCalledWith("c1"));
		await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
		expect(onCompacted).toHaveBeenCalled();
		// ~28k freed (30k - 2k)
		const [, opts] = toastSuccess.mock.calls[0];
		expect(opts.description).toMatch(/28k tokens freed/);
	});

	it("shows an info toast when there is nothing to compact", async () => {
		compactConversation.mockResolvedValue({
			compacted: false,
			turns_compacted: 0,
			tokens_before: 0,
			tokens_after: 0,
			message: "Nothing to compact yet.",
		});

		renderWithProviders(
			<CompactButton
				conversationId="c1"
				messages={[assistant(150_000)]}
				contextWindow={200_000}
			/>,
		);
		await userEvent.click(
			screen.getByRole("button", { name: /compact older turns/i }),
		);
		await waitFor(() => expect(toastInfo).toHaveBeenCalledWith("Nothing to compact yet."));
	});

	it("surfaces an error toast on failure", async () => {
		compactConversation.mockRejectedValue(new Error("boom"));
		renderWithProviders(
			<CompactButton
				conversationId="c1"
				messages={[assistant(150_000)]}
				contextWindow={200_000}
			/>,
		);
		await userEvent.click(
			screen.getByRole("button", { name: /compact older turns/i }),
		);
		await waitFor(() => expect(toastError).toHaveBeenCalled());
	});
});
