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
	useConversationStats: () => null,
}));

vi.mock("@/hooks/useUserPermissions", () => ({
	useUserPermissions: () => ({ isPlatformAdmin: false }),
}));

const mediaQueryState = {
	matches: true,
};

vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mediaQueryState.matches,
}));

import { ChatLayout } from "./ChatLayout";

function getSidebarShell(container: HTMLElement) {
	return container
		.querySelector('[data-marker="sidebar"]')
		?.parentElement?.parentElement;
}

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

	it("hides the sidebar when the close button is clicked", async () => {
		storeState.activeConversationId = "c-1";
		conversationRef.data = { title: "Title", agent_name: null };

		const { user, container } = renderWithProviders(<ChatLayout />);

		// The desktop close button is the PanelLeftClose icon — target by its
		// sibling svg presence on the sidebar-close button (first absolute btn).
		const buttons = container.querySelectorAll("button");
		// The close-sidebar button sits inside the sidebar wrapper.
		const closeBtn = Array.from(buttons).find(
			(b) => b.className.includes("absolute") && b.className.includes("right-2"),
		);
		expect(closeBtn).toBeTruthy();
		await user.click(closeBtn!);

		// After closing, a PanelLeft toggle appears in the header to reopen.
		const reopen = Array.from(container.querySelectorAll("button")).find(
			(b) =>
				b.className.includes("hidden lg:flex") &&
				b.querySelector("svg"),
		);
		expect(reopen).toBeTruthy();
	});

	it("keeps the sidebar out of the layout by default on mobile", () => {
		mediaQueryState.matches = false;

		const { container } = renderWithProviders(<ChatLayout />);

		expect(
			screen.getByRole("button", { name: /open chat sidebar/i }),
		).toBeInTheDocument();
		const sidebarShell = getSidebarShell(container);
		expect(sidebarShell?.className).toContain("w-0");
		expect(screen.getByText("no-convo|no-agent")).toBeInTheDocument();
	});

	it("closes the mobile sidebar after a conversation is selected", async () => {
		mediaQueryState.matches = false;
		const { user, container } = renderWithProviders(<ChatLayout />);

		await user.click(
			screen.getByRole("button", { name: /open chat sidebar/i }),
		);
		expect(
			getSidebarShell(container)?.className,
		).not.toContain("w-0");

		await user.click(
			screen.getByRole("button", { name: /mock select conversation/i }),
		);

		expect(
			getSidebarShell(container)?.className,
		).toContain("w-0");
	});

	it("opens the mobile sidebar above the app header as an opaque drawer", async () => {
		mediaQueryState.matches = false;
		const { user, container } = renderWithProviders(<ChatLayout />);

		await user.click(
			screen.getByRole("button", { name: /open chat sidebar/i }),
		);

		const sidebarShell = getSidebarShell(container);
		expect(sidebarShell?.className).toContain("z-50");
		expect(sidebarShell?.className).toContain("fixed");
		expect(sidebarShell?.className).toContain("bg-background");
		expect(sidebarShell?.style.width).toBe("20rem");
		expect(sidebarShell?.style.maxWidth).toBe("calc(100vw - 2rem)");
	});
});
