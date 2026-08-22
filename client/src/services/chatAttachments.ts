import { authFetch } from "@/lib/api-client";
import type { components } from "@/lib/v1";

export type AttachmentPublic = components["schemas"]["AttachmentPublic"];
export type AttachmentUploadResponse =
	components["schemas"]["AttachmentUploadResponse"];
export type ChatArtifactPublic = components["schemas"]["ChatArtifactPublic"];

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

export function isVideoAttachment(contentType: string): boolean {
	return contentType.startsWith("video/");
}

export function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
	return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
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

export async function listChatArtifacts(): Promise<ChatArtifactPublic[]> {
	const response = await authFetch("/api/chat/artifacts");
	if (!response.ok) throw new Error("Could not load your artifacts.");
	return response.json() as Promise<ChatArtifactPublic[]>;
}

export async function renameChatArtifact(
	attachmentId: string,
	filename: string,
): Promise<ChatArtifactPublic> {
	const response = await authFetch(`/api/chat/artifacts/${attachmentId}`, {
		method: "PATCH",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ filename }),
	});
	if (!response.ok) {
		const body = (await response.json().catch(() => ({}))) as {
			detail?: string;
		};
		throw new Error(body.detail || "Could not rename this artifact.");
	}
	return response.json() as Promise<ChatArtifactPublic>;
}

export async function deleteChatArtifact(attachmentId: string): Promise<void> {
	const response = await authFetch(`/api/chat/artifacts/${attachmentId}`, {
		method: "DELETE",
	});
	if (!response.ok) throw new Error("Could not delete this artifact.");
}

export function attachmentContentUrl(
	conversationId: string,
	attachmentId: string,
	options?: { download?: boolean; preview?: boolean },
): string {
	const base = conversationId
		? `/api/chat/conversations/${conversationId}/attachments/${attachmentId}/content`
		: `/api/sdk/artifacts/${attachmentId}/content`;
	const query = new URLSearchParams();
	if (options?.download) query.set("download", "true");
	if (options?.preview) query.set("preview", "true");
	return query.size > 0 ? `${base}?${query.toString()}` : base;
}

export async function downloadChatAttachment(
	conversationId: string,
	attachment: Pick<AttachmentPublic, "id" | "filename">,
): Promise<void> {
	const response = await authFetch(
		attachmentContentUrl(conversationId, attachment.id, { download: true }),
	);
	if (!response.ok) throw new Error("Download failed");
	const blobUrl = URL.createObjectURL(await response.blob());
	try {
		const anchor = document.createElement("a");
		anchor.href = blobUrl;
		anchor.download = attachment.filename;
		anchor.click();
	} finally {
		URL.revokeObjectURL(blobUrl);
	}
}
