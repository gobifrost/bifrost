/**
 * Component tests for ChatInput.
 *
 * Cover message send flow, disabled states, clear-after-send, @-trigger,
 * and the stop-vs-send button swap while loading.
 *
 * MentionPicker is mocked because it pulls useAgents + Radix Popover and
 * we're only asserting ChatInput's own behavior.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";

// Capture what MentionPicker receives so we can assert ChatInput opened it.
const mockMentionPicker = vi.fn();

vi.mock("./MentionPicker", () => ({
	MentionPicker: (props: {
		open: boolean;
		searchTerm: string;
		onSelect: (agent: { id: string; name: string }) => void;
	}) => {
		mockMentionPicker(props);
		// Expose a test button to simulate picking an agent when open.
		return props.open ? (
			<button
				type="button"
				onClick={() =>
					props.onSelect({ id: "agent-1", name: "SupportBot" })
				}
			>
				mention-picker-open:{props.searchTerm}
			</button>
		) : null;
	},
}));

import { ChatInput } from "./ChatInput";

describe("ChatInput — send behavior", () => {
	it("Send button is disabled when the textarea is empty", () => {
		renderWithProviders(<ChatInput onSend={vi.fn()} />);
		// Enabled send button is the only non-Coming-soon non-disabled button.
		// The send button is the last rendered button (empty state).
		const sendButtons = screen
			.getAllByRole("button")
			.filter((b) => !b.hasAttribute("title"));
		expect(sendButtons[sendButtons.length - 1]).toBeDisabled();
	});

	it("calls onSend with the trimmed message and clears the textarea", async () => {
		const onSend = vi.fn();
		const { user } = renderWithProviders(<ChatInput onSend={onSend} />);

		const textarea = screen.getByPlaceholderText(
			/reply/i,
		) as HTMLTextAreaElement;
		fireEvent.change(textarea, { target: { value: "  hello world  " } });

		// Press Enter to submit.
		await user.type(textarea, "{Enter}");

		expect(onSend).toHaveBeenCalledWith("hello world", [], "balanced");
		// Textarea is cleared post-send.
		expect(textarea.value).toBe("");
	});

	it("Shift+Enter does not submit (line break instead)", async () => {
		const onSend = vi.fn();
		const { user } = renderWithProviders(<ChatInput onSend={onSend} />);

		const textarea = screen.getByPlaceholderText(/reply/i);
		fireEvent.change(textarea, { target: { value: "line one" } });
		await user.type(textarea, "{Shift>}{Enter}{/Shift}");

		expect(onSend).not.toHaveBeenCalled();
	});

	it("does not send when disabled", async () => {
		const onSend = vi.fn();
		const { user } = renderWithProviders(
			<ChatInput onSend={onSend} disabled />,
		);

		const textarea = screen.getByPlaceholderText(/reply/i);
		fireEvent.change(textarea, { target: { value: "hello" } });
		await user.type(textarea, "{Enter}");

		expect(onSend).not.toHaveBeenCalled();
	});
});

describe("ChatInput — @ mentions", () => {
	it("opens the mention picker when the user types @ at the start", () => {
		renderWithProviders(<ChatInput onSend={vi.fn()} />);
		const textarea = screen.getByPlaceholderText(/reply/i);
		fireEvent.change(textarea, { target: { value: "@sup" } });

		// Our mocked MentionPicker surfaces its state via text content.
		expect(
			screen.getByText(/mention-picker-open:sup/),
		).toBeInTheDocument();
	});

	it("adds a mention chip on agent select and removes the typed @search", () => {
		const onSend = vi.fn();
		const { container } = renderWithProviders(<ChatInput onSend={onSend} />);
		const textarea = screen.getByPlaceholderText(
			/reply/i,
		) as HTMLTextAreaElement;

		fireEvent.change(textarea, { target: { value: "@Sup" } });
		// Picker rendered; click its stub select button.
		fireEvent.click(screen.getByText(/mention-picker-open:Sup/));

		// Chip appeared.
		expect(screen.getByText("SupportBot")).toBeInTheDocument();
		// The @Sup text is gone from the textarea.
		expect(textarea.value).toBe("");

		// Remove button for the chip is labelled via aria-label.
		const removeBtn = container.querySelector(
			'[aria-label="Remove SupportBot"]',
		) as HTMLButtonElement | null;
		expect(removeBtn).not.toBeNull();
	});

	it("sends the mention prefix when submitting with a chip but no text", async () => {
		const onSend = vi.fn();
		const { user } = renderWithProviders(<ChatInput onSend={onSend} />);
		const textarea = screen.getByPlaceholderText(
			/reply/i,
		) as HTMLTextAreaElement;

		fireEvent.change(textarea, { target: { value: "@Sup" } });
		fireEvent.click(screen.getByText(/mention-picker-open:Sup/));

		// Press Enter to send (placeholder changed to "Add a message...").
		await user.click(textarea);
		await user.keyboard("{Enter}");

		expect(onSend).toHaveBeenCalledWith("@[SupportBot]", [], "balanced");
	});
});

describe("ChatInput — attachments and model tier", () => {
	it("sends a staged file without requiring message text", async () => {
		const onSend = vi.fn();
		const { user, container } = renderWithProviders(
			<ChatInput onSend={onSend} />,
		);
		const file = new File(["hello"], "notes.txt", { type: "text/plain" });
		const input = container.querySelector('input[type="file"]') as HTMLInputElement;
		fireEvent.change(input, { target: { files: [file] } });

		expect(screen.getByText("notes.txt")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /send message/i }));
		expect(onSend).toHaveBeenCalledWith("", [file], "balanced");
	});
});

describe("ChatInput — loading / stop button", () => {
	it("shows the Stop button while loading and fires onStop", async () => {
		const onStop = vi.fn();
		const { user } = renderWithProviders(
			<ChatInput onSend={vi.fn()} isLoading onStop={onStop} />,
		);

		const stopButton = screen.getByTitle(/stop generation/i);
		await user.click(stopButton);
		expect(onStop).toHaveBeenCalledTimes(1);
	});
});
