/**
 * Component tests for ChatMessage.
 *
 * Cover the three axes that drive rendering:
 *   - user vs. assistant layout (user messages right-aligned bubble, assistant full-width)
 *   - markdown + code blocks render for assistant
 *   - streaming prop toggles the animate-pulse class
 *
 * We rely on react-markdown/react-syntax-highlighter running for real since
 * they're pure and fast; happy-dom is fine.
 */

import { describe, it, expect } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ChatMessage } from "./ChatMessage";
import type { components } from "@/lib/v1";

type MessagePublic = components["schemas"]["MessagePublic"];

function makeMessage(overrides: Partial<MessagePublic>): MessagePublic {
	return {
		id: "msg-1",
		conversation_id: "conv-1",
		role: "assistant",
		content: "hello",
		sequence: 0,
		created_at: "2026-04-20T12:00:00Z",
		...overrides,
	} as MessagePublic;
}

describe("ChatMessage — user messages", () => {
	it("renders a user message inside the user bubble", () => {
		const { container } = renderWithProviders(
			<ChatMessage
				message={makeMessage({ role: "user", content: "ping" })}
			/>,
		);
		expect(screen.getByText("ping")).toBeInTheDocument();
		// User messages use right-alignment.
		expect(container.querySelector(".justify-end")).not.toBeNull();
	});

	it("renders @[AgentName] mentions as an inline badge for user messages", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "user",
					content: "@[SupportBot] please help",
				})}
			/>,
		);
		// The badge displays the agent name as a span alongside the surrounding text.
		expect(screen.getByText("SupportBot")).toBeInTheDocument();
		expect(screen.getByText(/please help/)).toBeInTheDocument();
	});
});

describe("ChatMessage — assistant messages", () => {
	it("renders markdown headings for assistant content", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "# Hello\n\nA paragraph.",
				})}
			/>,
		);
		expect(
			screen.getByRole("heading", { level: 1, name: /hello/i }),
		).toBeInTheDocument();
		expect(screen.getByText(/a paragraph\./i)).toBeInTheDocument();
	});

	it("renders fenced code blocks via the syntax highlighter", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "```python\nprint('hi')\n```",
				})}
			/>,
		);
		// Syntax highlighter preserves the source text in the DOM.
		expect(screen.getByText(/print/)).toBeInTheDocument();
	});

	it("applies animate-pulse while streaming and drops it when done", () => {
		const { container, rerender } = renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "partial",
				})}
				isStreaming={true}
			/>,
		);
		expect(container.querySelector(".animate-pulse")).not.toBeNull();

		rerender(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "partial done",
				})}
				isStreaming={false}
			/>,
		);
		expect(container.querySelector(".animate-pulse")).toBeNull();
	});

	it("shows token counts on hover section when provided", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "done",
					token_count_input: 10,
					token_count_output: 42,
					duration_ms: 1500,
				})}
			/>,
		);
		expect(screen.getByText(/In: 10/)).toBeInTheDocument();
		expect(screen.getByText(/Out: 42/)).toBeInTheDocument();
		expect(screen.getByText(/1500ms/)).toBeInTheDocument();
	});

	it("renders the cost-tier icon for a known tier", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "done",
					cost_tier: "balanced",
				})}
			/>,
		);
		// Rendered as a Lucide icon (svg), not an emoji glyph, so the three
		// tiers read as one coherent monochrome set.
		const badge = screen.getByLabelText("Balanced tier");
		expect(badge.querySelector("svg")).not.toBeNull();
	});

	it("omits the cost-tier glyph for an unknown/missing tier", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "done",
					cost_tier: "mystery",
				})}
			/>,
		);
		expect(screen.queryByLabelText(/tier$/)).toBeNull();
	});
});
