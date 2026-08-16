import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const authFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ authFetch }));

import { ChatAttachmentList } from "./ChatAttachmentList";

describe("ChatAttachmentList", () => {
	beforeEach(() => authFetch.mockReset());

	it("opens a generated text artifact in the right-side preview sheet", async () => {
		let resolvePreview!: (response: Response) => void;
		authFetch.mockReturnValue(
			new Promise<Response>((resolve) => {
				resolvePreview = resolve;
			}),
		);
		const { user } = renderWithProviders(
			<ChatAttachmentList
				conversationId="conversation-1"
				variant="artifact"
				attachments={[
					{
						id: "artifact-1",
						filename: "report.md",
						content_type: "text/markdown",
						size_bytes: 23,
						kind: "artifact",
					},
				]}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /report\.md/i }));

		expect(screen.getByRole("dialog")).toBeInTheDocument();
		expect(screen.getByText("Preparing preview")).toBeInTheDocument();
		resolvePreview(
			new Response("generated artifact body", { status: 200 }),
		);
		await waitFor(() =>
			expect(
				screen.getByText("generated artifact body"),
			).toBeInTheDocument(),
		);
		expect(authFetch).toHaveBeenCalledWith(
			"/api/chat/conversations/conversation-1/attachments/artifact-1/content",
		);
		expect(
			screen.getByRole("button", { name: /download/i }),
		).toBeInTheDocument();
	});

	it("uses the full conversation width for generated artifacts", () => {
		renderWithProviders(
			<ChatAttachmentList
				conversationId="conversation-1"
				variant="artifact"
				attachments={[
					{
						id: "artifact-1",
						filename: "report.md",
						content_type: "text/markdown",
						size_bytes: 23,
						kind: "artifact",
					},
				]}
			/>,
		);

		expect(screen.getByRole("button", { name: /report\.md/i })).toHaveClass(
			"w-full",
			"max-w-none",
		);
	});

	it("retries a failed artifact preview", async () => {
		authFetch
			.mockResolvedValueOnce(new Response(null, { status: 503 }))
			.mockResolvedValueOnce(new Response("recovered preview", { status: 200 }));
		const { user } = renderWithProviders(
			<ChatAttachmentList
				conversationId="conversation-1"
				variant="artifact"
				attachments={[
					{
						id: "artifact-1",
						filename: "report.md",
						content_type: "text/markdown",
						size_bytes: 23,
						kind: "artifact",
					},
				]}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /report\.md/i }));
		await user.click(
			await screen.findByRole("button", { name: /retry preview/i }),
		);

		expect(await screen.findByText("recovered preview")).toBeInTheDocument();
		expect(authFetch).toHaveBeenCalledTimes(2);
	});
});
