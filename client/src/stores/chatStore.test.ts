import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "./chatStore";

describe("chatStore conversation selection", () => {
	beforeEach(() => {
		useChatStore.getState().reset();
	});

	it("keeps an active run streaming when route state reselects the same conversation", () => {
		const store = useChatStore.getState();
		store.setActiveConversation("conversation-1");
		store.startStreaming();

		useChatStore.getState().setActiveConversation("conversation-1");

		expect(useChatStore.getState().isStreaming).toBe(true);
	});

	it("clears streaming state when the user switches conversations", () => {
		const store = useChatStore.getState();
		store.setActiveConversation("conversation-1");
		store.startStreaming();

		useChatStore.getState().setActiveConversation("conversation-2");

		expect(useChatStore.getState().isStreaming).toBe(false);
	});
});
