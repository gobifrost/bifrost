/**
 * Component tests for ChatMessage.
 *
 * Cover the three axes that drive rendering:
 *   - user vs. assistant layout (user messages right-aligned bubble, assistant full-width)
 *   - markdown + code blocks render for assistant
 *   - streaming state is exposed without animating the message itself
 *
 * We rely on react-markdown/react-syntax-highlighter running for real since
 * they're pure and fast; happy-dom is fine.
 */

import { describe, it, expect, vi } from "vitest";
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

	it("renders attached files as preview controls", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "user",
					content: "Review this",
					attachments: [
						{
							id: "attachment-1",
							filename: "notes.txt",
							content_type: "text/plain",
							size_bytes: 12,
							kind: "attachment",
						},
					],
				})}
			/>,
		);
		expect(screen.getByRole("button", { name: "Preview notes.txt" })).toBeInTheDocument();
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

	it("preserves email addresses instead of rendering their domains as mentions", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "user",
					content: "Contact jack@example.com for access",
				})}
			/>,
		);

		expect(
			screen.getByRole("link", { name: "jack@example.com" }),
		).toHaveAttribute("href", "mailto:jack@example.com");
		expect(screen.queryByText("example")).not.toBeInTheDocument();
	});

	it("leaves bare @words as ordinary message text", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "user",
					content: "Ask @SupportBot for help",
				})}
			/>,
		);

		expect(screen.getByText(/Ask @SupportBot for help/)).toBeInTheDocument();
	});

	it("renders punctuation in canonical agent names without changing the name", () => {
		renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "user",
					content: '@[R&D "Tier 2" Agent] investigate',
				})}
			/>,
		);

		expect(screen.getByText('R&D "Tier 2" Agent')).toBeInTheDocument();
		expect(screen.getByText(/investigate/)).toBeInTheDocument();
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

	it("marks streaming content busy without fading the message", () => {
		const { container, rerender } = renderWithProviders(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "partial",
				})}
				isStreaming={true}
			/>,
		);
		expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
		expect(container.querySelector(".animate-pulse")).toBeNull();

		rerender(
			<ChatMessage
				message={makeMessage({
					role: "assistant",
					content: "partial done",
				})}
				isStreaming={false}
			/>,
		);
		expect(container.querySelector("[aria-busy]")).toBeNull();
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

	it("shows message time, copies content, and disables local file links", async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		vi.spyOn(navigator.clipboard, "writeText").mockImplementation(writeText);
		const { user } = renderWithProviders(
			<ChatMessage
				message={makeMessage({
					content: "[Local result](file:///tmp/report.pdf)",
				})}
			/>,
		);

		expect(screen.getByText(/Apr 20, 2026/i)).toBeInTheDocument();
		expect(screen.queryByRole("link", { name: "Local result" })).not.toBeInTheDocument();
		const copyButton = screen.getByRole("button", { name: "Copy message" });
		expect(copyButton).toHaveClass("size-11", "sm:size-6");
		expect(copyButton.parentElement).toHaveClass("opacity-100", "sm:opacity-0");
		await user.click(copyButton);
		expect(writeText).toHaveBeenCalledWith("[Local result](file:///tmp/report.pdf)");
	});
});
