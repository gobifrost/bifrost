import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const authFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ authFetch }));

import { ChatAttachmentList } from "./ChatAttachmentList";

describe("ChatAttachmentList", () => {
	beforeEach(() => {
		authFetch.mockReset();
		globalThis.URL.createObjectURL = vi.fn(() => "blob:media");
		globalThis.URL.revokeObjectURL = vi.fn();
	});

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

		await user.click(screen.getByRole("button", { name: "Preview report.md" }));

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

		expect(screen.getByRole("button", { name: "Preview report.md" })).toHaveClass(
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

		await user.click(screen.getByRole("button", { name: "Preview report.md" }));
		await user.click(
			await screen.findByRole("button", { name: /retry preview/i }),
		);

		expect(await screen.findByText("recovered preview")).toBeInTheDocument();
		expect(authFetch).toHaveBeenCalledTimes(2);
	});

	it("previews generated video and navigates the media gallery", async () => {
		authFetch.mockResolvedValue(
			new Response(new Blob(["video"]), {
				status: 200,
				headers: { "Content-Type": "video/mp4" },
			}),
		);
		const { user } = renderWithProviders(
			<ChatAttachmentList
				conversationId="conversation-1"
				variant="artifact"
				attachments={[
					{
						id: "video-1",
						filename: "Launch Loop.mp4",
						content_type: "video/mp4",
						size_bytes: 24,
						kind: "artifact",
					},
					{
						id: "image-1",
						filename: "Launch Concept.png",
						content_type: "image/png",
						size_bytes: 16,
						kind: "artifact",
					},
				]}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Preview Launch Loop.mp4" }));
		await waitFor(() => expect(document.querySelector("video")).toBeTruthy());
		expect(screen.getByText("1 / 2")).toBeInTheDocument();
		expect(screen.getByRole("dialog")).toHaveClass(
			"h-dvh",
			"w-full",
			"rounded-none",
		);
		const nextButton = screen.getByRole("button", { name: "Next media" });
		expect(nextButton).toHaveClass("size-11", "shrink-0", "sm:size-7");
		expect(screen.getByRole("button", { name: "Download" })).toHaveClass(
			"h-11",
			"sm:h-7",
		);
		await user.click(nextButton);
		expect(screen.getByRole("heading", { name: "Launch Concept.png" })).toBeInTheDocument();
	});
});
