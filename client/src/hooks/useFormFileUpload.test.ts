import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
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
