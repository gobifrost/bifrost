import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const services = vi.hoisted(() => ({
	listChatArtifacts: vi.fn(),
	renameChatArtifact: vi.fn(),
	deleteChatArtifact: vi.fn(),
}));

vi.mock("@/services/chatAttachments", async () => {
	const actual = await vi.importActual<typeof import("@/services/chatAttachments")>(
		"@/services/chatAttachments",
	);
	return { ...actual, ...services };
});

vi.mock("./FilePreviewSheet", () => ({
	FilePreviewSheet: ({ attachment }: { attachment: { filename: string } | null }) =>
		attachment ? <div data-testid="file-preview">{attachment.filename}</div> : null,
}));

import { ArtifactsLibrary } from "./ArtifactsLibrary";

const artifacts = [
	{
		id: "generated-1",
		conversation_id: "conversation-1",
		message_id: "message-1",
		filename: "Welcome Page.html",
		content_type: "text/html",
		size_bytes: 1024,
		kind: "artifact" as const,
		conversation_title: "Welcome work",
		created_at: "2026-08-15T00:00:00Z",
	},
	{
		id: "uploaded-1",
		conversation_id: "conversation-2",
		message_id: "message-2",
		filename: "Source Notes.txt",
		content_type: "text/plain",
		size_bytes: 50,
		kind: "attachment" as const,
		conversation_title: "Research",
		created_at: "2026-08-14T00:00:00Z",
	},
];

describe("ArtifactsLibrary", () => {
	beforeEach(() => {
		services.listChatArtifacts.mockReset();
		services.listChatArtifacts.mockResolvedValue(artifacts);
	});

	it("shows durable chat files and previews the selected artifact", async () => {
		const { user } = renderWithProviders(<ArtifactsLibrary />);

		expect(await screen.findByText("Welcome Page.html")).toBeInTheDocument();
		expect(screen.getByText("Source Notes.txt")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Preview Welcome Page.html" }),
		);
		expect(screen.getByTestId("file-preview")).toHaveTextContent("Welcome Page.html");
	});

	it("filters the library by origin", async () => {
		const { user } = renderWithProviders(<ArtifactsLibrary />);
		await screen.findByText("Welcome Page.html");

		const uploadedFilter = screen.getByRole("button", { name: "Uploaded" });
		expect(uploadedFilter).toHaveClass("min-h-11", "sm:min-h-7");
		await user.click(uploadedFilter);
		await waitFor(() =>
			expect(screen.queryByText("Welcome Page.html")).not.toBeInTheDocument(),
		);
		expect(screen.getByText("Source Notes.txt")).toBeInTheDocument();
	});
});
