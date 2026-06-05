import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderWithProviders } from "@/test-utils";

const paramsRef: { conversationId?: string } = {};
const storeState = {
	setActiveConversation: vi.fn(),
	reset: vi.fn(),
};

vi.mock("react-router-dom", async () => {
	const actual = await vi.importActual<typeof import("react-router-dom")>(
		"react-router-dom",
	);
	return {
		...actual,
		useParams: () => paramsRef,
		Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
	};
});

vi.mock("@/stores/chatStore", () => ({
	useChatStore: () => storeState,
}));

vi.mock("@/hooks/useLLMConfig", () => ({
	useLLMConfig: () => ({
		isConfigured: true,
		isPlatformAdmin: true,
		isLoading: false,
	}),
}));

vi.mock("@/components/chat", () => ({
	ChatLayout: ({ initialConversationId }: { initialConversationId?: string }) => (
		<div>{initialConversationId ?? "blank-chat"}</div>
	),
}));

import { Chat } from "./Chat";

beforeEach(() => {
	paramsRef.conversationId = undefined;
	storeState.setActiveConversation.mockReset();
	storeState.reset.mockReset();
});

describe("Chat page route state", () => {
	it("sets active conversation when the route has an id", () => {
		paramsRef.conversationId = "c-1";

		renderWithProviders(<Chat />);

		expect(storeState.setActiveConversation).toHaveBeenCalledWith("c-1");
	});

	it("clears stale active conversation when the route is /chat", () => {
		renderWithProviders(<Chat />);

		expect(storeState.setActiveConversation).toHaveBeenCalledWith(null);
	});
});
