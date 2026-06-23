import { getBifrostTransport } from "./transport";

export { getBifrostTransport, setBifrostTransport } from "./transport";

export type FileMode = "local" | "cloud";
export type SignedUrlMethod = "PUT" | "GET";

export interface FileOptions {
	location?: string;
	mode?: FileMode;
	scope?: string | null;
}

export interface FileListOptions extends FileOptions {
	includeMetadata?: boolean;
}

export interface FileListMetadataItem {
	path: string;
	etag: string;
	lastModified: string;
	updatedBy?: string | null;
}

export interface FileListResult {
	files: string[];
	filesMetadata: FileListMetadataItem[];
}

export interface SignedUrlOptions {
	method?: SignedUrlMethod;
	contentType?: string;
	location?: string;
	scope?: string | null;
}

export interface SignedUrlResult {
	url: string;
	path: string;
	expiresIn: number;
}

type FileUploadContent = string | Blob | Uint8Array | ArrayBuffer;

function getCsrfToken(): string {
	const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
	return match ? decodeURIComponent(match[1]) : "";
}

function requestDefaults(options: FileOptions = {}) {
	return {
		location: options.location ?? "workspace",
		mode: options.mode ?? "cloud",
		scope: options.scope ?? null,
	};
}

function signedDefaults(options: SignedUrlOptions = {}) {
	return {
		method: options.method ?? "PUT",
		content_type: options.contentType ?? "application/octet-stream",
		location: options.location ?? "workspace",
		scope: options.scope ?? null,
	};
}

async function errorText(response: Response): Promise<string> {
	const text = await response.text().catch(() => "");
	if (!text) return response.statusText;
	try {
		const body = JSON.parse(text) as { detail?: unknown; message?: unknown };
		const detail = body.detail ?? body.message;
		if (typeof detail === "string") return detail;
		if (detail !== undefined) return JSON.stringify(detail);
	} catch {
		// Plain text response.
	}
	return text;
}

export class FileAccessDeniedError extends Error {
	constructor(message = "Access denied") {
		super(message);
		this.name = "FileAccessDeniedError";
	}
}

export class FileNotFoundError extends Error {
	constructor(message = "File not found") {
		super(message);
		this.name = "FileNotFoundError";
	}
}

export class FilePolicyError extends Error {
	constructor(message = "File policy error") {
		super(message);
		this.name = "FilePolicyError";
	}
}

async function http<T>(
	path: string,
	init: RequestInit = {},
): Promise<T | null> {
	const method = (init.method ?? "GET").toUpperCase();
	const transport = getBifrostTransport();
	const usingProvider = Boolean(transport.baseUrl || transport.headers);
	const csrfHeaders: Record<string, string> =
		usingProvider || method === "GET" || method === "HEAD"
			? {}
			: { "X-CSRF-Token": getCsrfToken() };
	const url = transport.baseUrl
		? `${transport.baseUrl.replace(/\/$/, "")}${path}`
		: path;
	const doFetch = transport.fetchImpl ?? fetch;
	const response = await doFetch(url, {
		...init,
		credentials: usingProvider ? "omit" : "include",
		headers: {
			"content-type": "application/json",
			...csrfHeaders,
			...(transport.headers ?? {}),
			...(init.headers ?? {}),
		},
	});

	if (response.status === 403) {
		throw new FileAccessDeniedError(await errorText(response));
	}
	if (response.status === 404) {
		throw new FileNotFoundError(await errorText(response));
	}
	if (response.status === 400 || response.status === 422) {
		throw new FilePolicyError(await errorText(response));
	}
	if (response.status === 204) return null;
	if (!response.ok) {
		throw new Error(`files: ${response.status} ${await errorText(response)}`);
	}
	return (await response.json()) as T;
}

function toBase64(bytes: Uint8Array): string {
	let binary = "";
	for (const byte of bytes) binary += String.fromCharCode(byte);
	return btoa(binary);
}

function fromBase64(content: string): Uint8Array {
	const binary = atob(content);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i += 1) {
		bytes[i] = binary.charCodeAt(i);
	}
	return bytes;
}

async function toBytes(
	content: string | Uint8Array | ArrayBuffer | Blob,
): Promise<Uint8Array> {
	if (typeof content === "string") return new TextEncoder().encode(content);
	if (content instanceof Uint8Array) return content;
	if (content instanceof ArrayBuffer) return new Uint8Array(content);
	return new Uint8Array(await content.arrayBuffer());
}

async function toUploadBlob(
	content: FileUploadContent,
	contentType: string,
): Promise<Blob> {
	if (content instanceof Blob) return content;
	const bytes = await toBytes(content);
	const copy = new Uint8Array(bytes.byteLength);
	copy.set(bytes);
	return new Blob([copy.buffer], { type: contentType });
}

function normalizeListResponse(response: {
	files?: string[];
	files_metadata?: Array<{
		path: string;
		etag: string;
		last_modified: string;
		updated_by?: string | null;
	}>;
}): FileListResult {
	return {
		files: response.files ?? [],
		filesMetadata: (response.files_metadata ?? []).map((item) => ({
			path: item.path,
			etag: item.etag,
			lastModified: item.last_modified,
			updatedBy: item.updated_by ?? null,
		})),
	};
}

function normalizeSignedUrl(response: {
	url: string;
	path: string;
	expires_in: number;
}): SignedUrlResult {
	return {
		url: response.url,
		path: response.path,
		expiresIn: response.expires_in,
	};
}

export const files = {
	async read(path: string, options: FileOptions = {}): Promise<string> {
		const response = await http<{ content: string; binary: boolean }>(
			"/api/files/read",
			{
				method: "POST",
				body: JSON.stringify({
					path,
					...requestDefaults(options),
					binary: false,
				}),
			},
		);
		return response!.content;
	},

	async readBytes(
		path: string,
		options: FileOptions = {},
	): Promise<Uint8Array> {
		const response = await http<{ content: string; binary: boolean }>(
			"/api/files/read",
			{
				method: "POST",
				body: JSON.stringify({
					path,
					...requestDefaults(options),
					binary: true,
				}),
			},
		);
		return fromBase64(response!.content);
	},

	async write(
		path: string,
		content: string,
		options: FileOptions = {},
	): Promise<void> {
		await http("/api/files/write", {
			method: "POST",
			body: JSON.stringify({
				path,
				content,
				...requestDefaults(options),
				binary: false,
			}),
		});
	},

	async writeBytes(
		path: string,
		content: Uint8Array | ArrayBuffer | Blob,
		options: FileOptions = {},
	): Promise<void> {
		await http("/api/files/write", {
			method: "POST",
			body: JSON.stringify({
				path,
				content: toBase64(await toBytes(content)),
				...requestDefaults(options),
				binary: true,
			}),
		});
	},

	async delete(path: string, options: FileOptions = {}): Promise<void> {
		await http("/api/files/delete", {
			method: "POST",
			body: JSON.stringify({
				path,
				...requestDefaults(options),
			}),
		});
	},

	async list(
		directory = "",
		options: FileListOptions = {},
	): Promise<FileListResult> {
		const response = await http<{
			files: string[];
			files_metadata?: Array<{
				path: string;
				etag: string;
				last_modified: string;
				updated_by?: string | null;
			}>;
		}>("/api/files/list", {
			method: "POST",
			body: JSON.stringify({
				directory,
				...requestDefaults(options),
				include_metadata: options.includeMetadata ?? false,
			}),
		});
		return normalizeListResponse(response!);
	},

	async exists(path: string, options: FileOptions = {}): Promise<boolean> {
		const response = await http<{ exists: boolean }>("/api/files/exists", {
			method: "POST",
			body: JSON.stringify({
				path,
				...requestDefaults(options),
			}),
		});
		return response!.exists;
	},

	async signedUrl(
		path: string,
		options: SignedUrlOptions = {},
	): Promise<SignedUrlResult> {
		const response = await http<{
			url: string;
			path: string;
			expires_in: number;
		}>("/api/files/signed-url", {
			method: "POST",
			body: JSON.stringify({
				path,
				...signedDefaults(options),
			}),
		});
		return normalizeSignedUrl(response!);
	},

	async signedUrls(
		paths: string[],
		options: SignedUrlOptions = {},
	): Promise<SignedUrlResult[]> {
		const response = await http<{
			results: Array<{
				path: string;
				resolved_path?: string | null;
				method: SignedUrlMethod;
				url?: string | null;
				expires_in: number;
				error?: string | null;
				status_code: number;
			}>;
		}>("/api/files/signed-urls", {
			method: "POST",
			body: JSON.stringify({
				requests: paths.map((path) => ({
					path,
					...signedDefaults(options),
				})),
			}),
		});
		return response!.results.map((item) => {
			if (!item.url) {
				throw new FileAccessDeniedError(
					item.error || `Unable to sign ${item.path}`,
				);
			}
			return {
				url: item.url,
				path: item.resolved_path ?? item.path,
				expiresIn: item.expires_in,
			};
		});
	},

	async upload(
		path: string,
		content: FileUploadContent,
		options: Omit<SignedUrlOptions, "method"> = {},
	): Promise<SignedUrlResult> {
		const contentType =
			options.contentType ??
			(content instanceof Blob && content.type
				? content.type
				: "application/octet-stream");
		const signed = await files.signedUrl(path, {
			...options,
			method: "PUT",
			contentType,
		});
		const body = await toUploadBlob(content, contentType);
		const response = await fetch(signed.url, {
			method: "PUT",
			headers: { "content-type": contentType },
			body,
		});
			if (!response.ok) {
				throw new Error(
					`files.upload: ${response.status} ${await errorText(response)}`,
				);
			}
			await http("/api/files/complete-upload", {
				method: "POST",
				body: JSON.stringify({
					path,
					content_type: contentType,
					size_bytes: body.size,
					location: options.location ?? "workspace",
					scope: options.scope ?? null,
				}),
			});
			return signed;
		},

	async download(
		path: string,
		options: Omit<SignedUrlOptions, "method"> = {},
	): Promise<Blob> {
		const signed = await files.signedUrl(path, {
			...options,
			method: "GET",
		});
		const response = await fetch(signed.url, { method: "GET" });
		if (!response.ok) {
			throw new Error(
				`files.download: ${response.status} ${await errorText(response)}`,
			);
		}
		return response.blob();
	},
};
