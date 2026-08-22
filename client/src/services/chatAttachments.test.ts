import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ authFetch }));

import {
	MAX_ATTACHMENT_SIZE_BYTES,
	attachmentContentUrl,
	deleteChatArtifact,
	deleteUnboundChatAttachment,
	downloadChatAttachment,
	formatBytes,
	isImageAttachment,
	isVideoAttachment,
	listChatArtifacts,
	renameChatArtifact,
	uploadChatAttachments,
	validateAttachment,
} from "./chatAttachments";

describe("chatAttachments", () => {
	beforeEach(() => authFetch.mockReset());

	it("validates supported files and size limits", () => {
		expect(
			validateAttachment(
				new File(["hello"], "notes.txt", { type: "text/plain" }),
			),
		).toBeNull();
		expect(validateAttachment(new File(["hello"], "notes.md"))).toBeNull();
		expect(
			validateAttachment(
				new File(["x"], "archive.zip", { type: "application/zip" }),
			),
		).toMatch(/not a supported/i);
		const oversized = {
			name: "large.pdf",
			type: "application/pdf",
			size: MAX_ATTACHMENT_SIZE_BYTES + 1,
		} as File;
		expect(validateAttachment(oversized)).toMatch(/too large/i);
	});

	it("builds owner-scoped preview and download URLs", () => {
		expect(attachmentContentUrl("conversation", "attachment")).toBe(
			"/api/chat/conversations/conversation/attachments/attachment/content",
		);
		expect(
			attachmentContentUrl("conversation", "attachment", {
				download: true,
			}),
		).toMatch(/download=true$/);
		expect(
			attachmentContentUrl("conversation", "attachment", {
				preview: true,
			}),
		).toMatch(/preview=true$/);
		expect(attachmentContentUrl("", "artifact-1")).toBe(
			"/api/sdk/artifacts/artifact-1/content",
		);
		expect(isImageAttachment("image/webp")).toBe(true);
		expect(isVideoAttachment("video/mp4")).toBe(true);
		expect(formatBytes(1024)).toBe("1 KB");
	});

	it("uploads files as multipart and discards unbound uploads", async () => {
		authFetch
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						attachments: [
							{
								id: "attachment-1",
								filename: "notes.txt",
								content_type: "text/plain",
								size_bytes: 5,
							},
						],
					}),
					{ status: 200 },
				),
			)
			.mockResolvedValueOnce(new Response(null, { status: 204 }));

		const file = new File(["hello"], "notes.txt", { type: "text/plain" });
		const uploaded = await uploadChatAttachments("conversation-1", [file]);
		expect(uploaded.attachments[0].id).toBe("attachment-1");
		expect(authFetch.mock.calls[0][1]).toMatchObject({
			method: "POST",
			body: expect.any(FormData),
		});

		await deleteUnboundChatAttachment("conversation-1", "attachment-1");
		expect(authFetch).toHaveBeenLastCalledWith(
			"/api/chat/conversations/conversation-1/attachments/attachment-1",
			{ method: "DELETE" },
		);
	});

	it("lists, renames, and deletes durable artifacts", async () => {
		const artifact = {
			id: "artifact-1",
			conversation_id: "conversation-1",
			message_id: "message-1",
			filename: "Welcome Page.html",
			content_type: "text/html",
			size_bytes: 100,
			kind: "artifact",
			created_at: "2026-08-15T00:00:00Z",
		};
		authFetch
			.mockResolvedValueOnce(
				new Response(JSON.stringify([artifact]), { status: 200 }),
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({ ...artifact, filename: "Bifrost Welcome.html" }),
					{ status: 200 },
				),
			)
			.mockResolvedValueOnce(new Response(null, { status: 204 }));

		expect(await listChatArtifacts()).toEqual([artifact]);
		await renameChatArtifact("artifact-1", "Bifrost Welcome.html");
		expect(authFetch).toHaveBeenNthCalledWith(
			2,
			"/api/chat/artifacts/artifact-1",
			expect.objectContaining({
				method: "PATCH",
				body: JSON.stringify({ filename: "Bifrost Welcome.html" }),
			}),
		);
		await deleteChatArtifact("artifact-1");
		expect(authFetch).toHaveBeenLastCalledWith(
			"/api/chat/artifacts/artifact-1",
			{ method: "DELETE" },
		);
	});

	it("downloads an attachment with its stored filename", async () => {
		const click = vi.fn();
		const createElement = vi.spyOn(document, "createElement");
		createElement.mockReturnValue({ click } as unknown as HTMLAnchorElement);
		vi.stubGlobal("URL", {
			createObjectURL: vi.fn(() => "blob:download"),
			revokeObjectURL: vi.fn(),
		});
		authFetch.mockResolvedValue(new Response("file", { status: 200 }));

		await downloadChatAttachment("conversation-1", {
			id: "artifact-1",
			filename: "Field Report.pdf",
		});

		expect(authFetch).toHaveBeenCalledWith(
			"/api/chat/conversations/conversation-1/attachments/artifact-1/content?download=true",
		);
		expect(click).toHaveBeenCalledOnce();
		expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:download");
		createElement.mockRestore();
		vi.unstubAllGlobals();
	});
});
