import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

vi.mock("@/components/chat/ChatLayout", () => ({
	ChatLayout: ({ view }: { view?: string }) => <div>Chat layout: {view}</div>,
}));

import { ChatArtifacts } from "./ChatArtifacts";

describe("ChatArtifacts", () => {
	it("opens the chat shell in artifact-library mode", () => {
		renderWithProviders(<ChatArtifacts />);
		expect(screen.getByText("Chat layout: artifacts")).toBeInTheDocument();
	});
});
