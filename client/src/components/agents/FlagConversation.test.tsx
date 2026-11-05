import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import type { components } from "@/lib/v1";

import { FlagConversation } from "./FlagConversation";

type FlagConversationResponse =
	components["schemas"]["FlagConversationResponse"];

function makeConversation(
	messages: FlagConversationResponse["messages"],
): FlagConversationResponse {
	return {
		id: "00000000-0000-0000-0000-0000000000c1",
		run_id: "00000000-0000-0000-0000-0000000000a1",
		messages,
		created_at: "2026-04-21T10:00:00Z",
		last_updated_at: "2026-04-21T10:00:00Z",
	};
}

describe("FlagConversation", () => {
	it("shows evaluation failure without a passing or wrong prediction", () => {
		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([
					{
						kind: "dryrun",
						before: "Old answer",
						after: "Unable to evaluate (invalid JSON)",
						predicted: "up",
						evaluation_failed: true,
					},
				])}
				onSend={() => {}}
			/>,
		);
		expect(screen.getByText("Evaluation Failed")).toBeInTheDocument();
		expect(screen.queryByText(/dry-run passed/i)).not.toBeInTheDocument();
		expect(
			screen.queryByText(/dry-run still wrong/i),
		).not.toBeInTheDocument();
		expect(
			screen.getByText(/No behavior prediction is available/),
		).toBeInTheDocument();
	});
	it("renders an empty-state bubble when no messages", () => {
		renderWithProviders(
			<FlagConversation conversation={null} onSend={() => {}} />,
		);
		expect(
			screen.getByText(/flag this run and tell me what went wrong/i),
		).toBeInTheDocument();
	});

	it("renders an empty-state bubble when conversation has zero messages", () => {
		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([])}
				onSend={() => {}}
			/>,
		);
		expect(
			screen.getByText(/flag this run and tell me what went wrong/i),
		).toBeInTheDocument();
	});

	it("renders a user bubble for user-kind messages", () => {
		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([
					{
						kind: "user",
						content: "It told the customer the wrong policy.",
					},
				])}
				onSend={() => {}}
			/>,
		);
		const bubble = screen.getByText(
			/it told the customer the wrong policy/i,
		);
		expect(bubble).toBeInTheDocument();
		expect(bubble.closest("[data-bubble-kind='user']")).not.toBeNull();
	});

	it("renders an assistant bubble for assistant-kind messages", () => {
		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([
					{
						kind: "assistant",
						content: "Got it — let me look at the transcript.",
					},
				])}
				onSend={() => {}}
			/>,
		);
		const bubble = screen.getByText(/got it — let me look/i);
		expect(bubble).toBeInTheDocument();
		expect(bubble.closest("[data-bubble-kind='assistant']")).not.toBeNull();
	});

	it("renders a proposal bubble with diff lines", () => {
		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([
					{
						kind: "proposal",
						summary: "Tighten the routing rule",
						diff: [
							{ op: "remove", text: "old line" },
							{ op: "add", text: "new line" },
						],
					},
				])}
				onSend={() => {}}
			/>,
		);
		expect(screen.getByText(/proposed change/i)).toBeInTheDocument();
		expect(
			screen.getByText(/tighten the routing rule/i),
		).toBeInTheDocument();
		expect(screen.getByText(/old line/)).toBeInTheDocument();
		expect(screen.getByText(/new line/)).toBeInTheDocument();
	});

	it("renders dryrun bubble with before/after", () => {
		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([
					{
						kind: "dryrun",
						before: "old answer",
						after: "new answer",
						predicted: "up",
						evaluation_failed: false,
					},
				])}
				onSend={() => {}}
			/>,
		);
		expect(screen.getByText(/dry-run passed/i)).toBeInTheDocument();
		expect(screen.getByText(/old answer/)).toBeInTheDocument();
		expect(screen.getByText(/new answer/)).toBeInTheDocument();
	});

	it("calls onSend with text when ChatComposer submits", async () => {
		const onSend = vi.fn();
		const { user } = renderWithProviders(
			<FlagConversation conversation={null} onSend={onSend} />,
		);
		const ta = screen.getByPlaceholderText(/what should it have done/i);
		await user.click(ta);
		await user.keyboard("hi{Enter}");
		expect(onSend).toHaveBeenCalledWith("hi");
	});

	it("shows the Thinking… loader when pending", () => {
		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([
					{ kind: "user", content: "still wrong" },
				])}
				onSend={() => {}}
				pending
			/>,
		);
		expect(screen.getByText(/thinking/i)).toBeInTheDocument();
	});

	it("disables the composer send button when pending", () => {
		renderWithProviders(
			<FlagConversation conversation={null} onSend={() => {}} pending />,
		);
		expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
	});

	it("uses reduced-motion scroll behavior when the user prefers less motion", () => {
		const scrollTo = vi.fn();
		const matchMedia = vi.fn((query: string) => ({
			matches: query.includes("prefers-reduced-motion"),
			media: query,
			onchange: null,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
			dispatchEvent: vi.fn(),
		}));

		Object.defineProperty(HTMLElement.prototype, "scrollTo", {
			configurable: true,
			value: scrollTo,
		});
		Object.defineProperty(window, "matchMedia", {
			configurable: true,
			value: matchMedia,
		});

		renderWithProviders(
			<FlagConversation
				conversation={makeConversation([
					{ kind: "user", content: "still wrong" },
				])}
				onSend={() => {}}
			/>,
		);

		expect(scrollTo).toHaveBeenCalledWith(
			expect.objectContaining({ behavior: "auto" }),
		);
	});
});
