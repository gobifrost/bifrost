import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const post = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ apiClient: { POST: post } }));
import { useFormFileUpload } from "./useFormFileUpload";
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

beforeEach(() => post.mockReset());
afterEach(() => vi.unstubAllGlobals());
it("sends every signed header once without duplicating Content-Type", async () => {
	class Upload extends EventTarget {
		upload = new EventTarget();
		status = 201;
		open = vi.fn();
		setRequestHeader = vi.fn();
		send() { queueMicrotask(() => this.dispatchEvent(new Event("load"))); }
	}
	const xhr = new Upload();
	vi.stubGlobal("XMLHttpRequest", class { constructor() { return xhr; } });
	post.mockResolvedValue({ data: {
		upload_url: "https://blob.invalid/upload",
		blob_uri: "uploads/form/file.txt",
		headers: { "x-ms-blob-type": "BlockBlob", "content-type": "text/plain" },
	} });
	const { result } = renderHook(() => useFormFileUpload("form"));
	await act(async () => { await result.current.uploadFile(new File(["bytes"], "file.txt", { type: "text/plain" })); });
	expect(xhr.open).toHaveBeenCalledWith("PUT", "https://blob.invalid/upload");
	expect(xhr.setRequestHeader).toHaveBeenCalledTimes(2);
	expect(Object.fromEntries(xhr.setRequestHeader.mock.calls)).toEqual({
		"content-type": "text/plain", "x-ms-blob-type": "BlockBlob",
	});
});
