import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type AttachmentPublic = components["schemas"]["AttachmentPublic"];
export type AttachmentUploadResponse =
	components["schemas"]["AttachmentUploadResponse"];

export const MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024;
export const MAX_ATTACHMENTS_PER_MESSAGE = 5;

const ALLOWED_TYPES = new Set([
	"image/png",
	"image/jpeg",
	"image/webp",
	"image/gif",
	"application/pdf",
	"text/plain",
	"text/markdown",
	"text/csv",
	"application/csv",
	"application/json",
	"text/json",
	"application/x-yaml",
	"text/yaml",
	"text/x-yaml",
]);
const ALLOWED_TEXT_EXTENSIONS = new Set([
	"txt",
	"md",
	"markdown",
	"csv",
	"json",
	"yaml",
	"yml",
]);

export function isImageAttachment(contentType: string): boolean {
	return contentType.startsWith("image/");
}

export function validateAttachment(file: File): string | null {
	if (file.size <= 0) return `${file.name} is empty.`;
	if (file.size > MAX_ATTACHMENT_SIZE_BYTES) {
		return `${file.name} is too large (maximum 25 MB).`;
	}
	const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
	const knownTextFile = !file.type && ALLOWED_TEXT_EXTENSIONS.has(extension);
	if (
		!knownTextFile &&
		!ALLOWED_TYPES.has(file.type) &&
		!file.type.startsWith("text/")
	) {
		return `${file.name} is not a supported image, PDF, CSV, or text file.`;
	}
	return null;
}

export async function uploadChatAttachments(
	conversationId: string,
	files: File[],
): Promise<AttachmentUploadResponse> {
	if (files.length > MAX_ATTACHMENTS_PER_MESSAGE) {
		throw new Error(
			`Attach no more than ${MAX_ATTACHMENTS_PER_MESSAGE} files per message.`,
		);
	}
	const formData = new FormData();
	for (const file of files) formData.append("files", file);
	const response = await authFetch(
		`/api/chat/conversations/${conversationId}/attachments`,
		{ method: "POST", body: formData },
	);
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as {
			detail?: string;
		};
		throw new Error(body.detail || "Could not upload the attached files.");
	}
	return response.json() as Promise<AttachmentUploadResponse>;
}

export async function deleteUnboundChatAttachment(
	conversationId: string,
	attachmentId: string,
): Promise<void> {
	const response = await authFetch(
		`/api/chat/conversations/${conversationId}/attachments/${attachmentId}`,
		{ method: "DELETE" },
	);
	if (!response.ok && response.status !== 404) {
		throw new Error("Could not discard the uploaded file.");
	}
}

export function attachmentContentUrl(
	conversationId: string,
	attachmentId: string,
	options?: { download?: boolean },
): string {
	const base = `/api/chat/conversations/${conversationId}/attachments/${attachmentId}/content`;
	return options?.download ? `${base}?download=true` : base;
}
