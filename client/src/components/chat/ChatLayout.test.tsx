/**
 * Component tests for ChatLayout.
 *
 * ChatLayout wires the sidebar + window and owns the desktop sidebar toggle
 * + initial-conversation seeding. We stub the two children so we can focus
 * on the layout behavior itself.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

// Stub the children — their own tests cover their behavior.
vi.mock("./ChatSidebar", () => ({
	ChatSidebar: ({
		onConversationSelected,
	}: {
		onConversationSelected?: () => void;
	}) => (
		<div data-marker="sidebar">
			<button type="button" onClick={onConversationSelected}>
				Mock select conversation
			</button>
		</div>
	),
}));
vi.mock("./ChatWindow", () => ({
	ChatWindow: ({
		conversationId,
		agentName,
	}: {
		conversationId?: string;
		agentName?: string | null;
	}) => (
		<div data-marker="window">
			{conversationId ?? "no-convo"}|{agentName ?? "no-agent"}
		</div>
	),
}));
vi.mock("./ArtifactsLibrary", () => ({
	ArtifactsLibrary: () => <div data-marker="artifacts-library">Artifacts library</div>,
}));

// Store state ChatLayout reads from.
const storeState = {
	activeConversationId: null as string | null,
	setActiveConversation: vi.fn(),
};

vi.mock("@/stores/chatStore", () => ({
	useChatStore: <T,>(selector: (s: typeof storeState) => T) =>
		selector(storeState),
}));

// Hooks: stub to return predictable data.
const conversationRef: { data: Record<string, unknown> | undefined } = {
	data: undefined,
};
vi.mock("@/hooks/useChat", () => ({
	useConversation: () => ({ data: conversationRef.data }),
}));

const mediaQueryState = {
	matches: true,
};

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mediaQueryState.matches,
}));

import { ChatLayout } from "./ChatLayout";

beforeEach(() => {
	mediaQueryState.matches = true;
	storeState.activeConversationId = null;
	storeState.setActiveConversation.mockReset();
	conversationRef.data = undefined;
});

describe("ChatLayout — composition", () => {
	it("renders both the sidebar and the chat window", () => {
		const { container } = renderWithProviders(<ChatLayout />);
		expect(container.querySelector('[data-marker="sidebar"]')).not.toBeNull();
		expect(container.querySelector('[data-marker="window"]')).not.toBeNull();
		expect(screen.getByText("no-convo|no-agent")).toBeInTheDocument();
	});

	it("seeds the active conversation from initialConversationId prop", () => {
		renderWithProviders(<ChatLayout initialConversationId="c-99" />);
		expect(storeState.setActiveConversation).toHaveBeenCalledWith("c-99");
	});

	it("renders the conversation title in the header when one is active", () => {
		storeState.activeConversationId = "c-1";
		conversationRef.data = {
			title: "My Chat",
			agent_name: "SupportBot",
		};

		renderWithProviders(<ChatLayout />);

		// Title appears in the header H1.
		expect(
			screen.getByRole("heading", { level: 1, name: "My Chat" }),
		).toBeInTheDocument();
		// Subtitle is the agent name.
		expect(screen.getByText(/with supportbot/i)).toBeInTheDocument();
	});

	it("forwards agent name to ChatWindow", () => {
		storeState.activeConversationId = "c-1";
		conversationRef.data = { title: "Title", agent_name: "DevBot" };

		renderWithProviders(<ChatLayout />);

		// Stubbed ChatWindow renders `${conversationId}|${agentName}`.
		expect(screen.getByText("c-1|DevBot")).toBeInTheDocument();
	});

	it("allows desktop artifacts navigation to be reopened", async () => {
		const { user } = renderWithProviders(<ChatLayout view="artifacts" />);
		await user.click(screen.getByRole("button", { name: "Close chat sidebar" }));
		expect(screen.queryByRole("complementary", { name: "Chat navigation" })).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Open chat sidebar" }));
		expect(screen.getByRole("complementary", { name: "Chat navigation" })).toBeInTheDocument();
	});

	it("keeps mobile navigation closed until requested", () => {
		mediaQueryState.matches = false;
		renderWithProviders(<ChatLayout />);
		expect(screen.getByRole("button", { name: "Open chat sidebar" })).toBeInTheDocument();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(screen.getByText("no-convo|no-agent")).toBeInTheDocument();
	});

	it("closes mobile navigation on selection and restores trigger focus", async () => {
		mediaQueryState.matches = false;
		const { user } = renderWithProviders(<ChatLayout />);
		const trigger = screen.getByRole("button", { name: "Open chat sidebar" });
		await user.click(trigger);
		expect(screen.getByRole("dialog", { name: "Chat navigation" })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Mock select conversation" }));
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(trigger).toHaveFocus();
	});

	it("supports Escape from mobile artifact navigation", async () => {
		mediaQueryState.matches = false;
		const { user } = renderWithProviders(<ChatLayout view="artifacts" />);
		const trigger = screen.getByRole("button", { name: "Open chat sidebar" });
		await user.click(trigger);
		expect(screen.getByRole("dialog", { name: "Chat navigation" })).toBeInTheDocument();
		await user.keyboard("{Escape}");
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
		expect(trigger).toHaveFocus();
	});
});
