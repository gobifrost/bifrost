import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiClient: { POST: post } }));

import { useFormFileUpload } from "./useFormFileUpload";

class UploadRequest extends EventTarget {
	upload = new EventTarget();
	status = 201;
	headers = new Headers();
	open = vi.fn();
	body: File | undefined;
	setRequestHeader(name: string, value: string) {
		this.headers.append(name, value);
	}
	send(file: File) {
		this.body = file;
		queueMicrotask(() => this.dispatchEvent(new Event("load")));
	}
}

describe("useFormFileUpload", () => {
	let request: UploadRequest;
	beforeEach(() => {
		post.mockReset();
		request = new UploadRequest();
		vi.stubGlobal(
			"XMLHttpRequest",
			class {
				constructor() {
					return request;
				}
			},
		);
	});
	afterEach(() => vi.unstubAllGlobals());

	it.each([
		{
			name: "Blob",
			headers: {
				"Content-Type": "text/plain",
				"x-ms-blob-type": "BlockBlob",
			},
		},
		{ name: "S3", headers: { "Content-Type": "text/plain" } },
		{ name: "an older S3 response", headers: undefined },
	])(
		"forwards required headers for $name without duplicating Content-Type",
		async ({ headers }) => {
			post.mockResolvedValue({
				data: {
					upload_url: "https://storage.example.invalid/fixture",
					headers,
					blob_uri: "form/fixture/document.txt",
				},
				error: undefined,
			});
			const file = new File(["fixture"], "document.txt", {
				type: "text/plain",
			});
			const { result } = renderHook(() =>
				useFormFileUpload("form", { fieldName: "document" }),
			);
			await act(async () => {
				expect(await result.current.uploadFile(file)).toBe(
					"form/fixture/document.txt",
				);
			});
			expect(request.open).toHaveBeenCalledWith(
				"PUT",
				"https://storage.example.invalid/fixture",
			);
			expect(request.headers.get("content-type")).toBe("text/plain");
			expect(request.headers.get("x-ms-blob-type")).toBe(
				headers?.["x-ms-blob-type"] ?? null,
			);
			expect(request.body).toBe(file);
			expect(result.current.uploadState.status).toBe("completed");
		},
	);

	it("uses the API's signed content type instead of appending a second value", async () => {
		post.mockResolvedValue({
			data: {
				upload_url: "https://storage.example.invalid/fixture",
				headers: { "content-type": "application/octet-stream" },
				blob_uri: "fixture",
			},
		});
		const { result } = renderHook(() => useFormFileUpload("form"));
		await act(async () => {
			await result.current.uploadFile(
				new File(["fixture"], "file.txt", { type: "text/plain" }),
			);
		});
		expect(request.headers.get("content-type")).toBe(
			"application/octet-stream",
		);
	});
});

it("requests an upload location in the same organization as execution", async () => {
	post.mockResolvedValue({ error: { message: "Storage unavailable" } });
	const { result } = renderHook(() =>
		useFormFileUpload("form-1", {
			orgId: "contoso",
			fieldName: "attachment",
		}),
	);
	await act(async () => {
		await expect(
			result.current.uploadFile(
				new File(["report"], "report.txt", { type: "text/plain" }),
			),
		).rejects.toThrow("Storage unavailable");
	});
	expect(post).toHaveBeenCalledWith("/api/forms/{form_id}/upload", {
		params: { path: { form_id: "form-1" }, query: { org_id: "contoso" } },
		body: {
			file_name: "report.txt",
			file_size: 6,
			content_type: "text/plain",
			field_name: "attachment",
		},
	});
});
