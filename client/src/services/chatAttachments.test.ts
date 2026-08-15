import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ authFetch }));

import {
	MAX_ATTACHMENT_SIZE_BYTES,
	attachmentContentUrl,
	deleteUnboundChatAttachment,
	isImageAttachment,
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
			attachmentContentUrl("conversation", "attachment", { download: true }),
		).toMatch(/download=true$/);
		expect(isImageAttachment("image/webp")).toBe(true);
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
});
