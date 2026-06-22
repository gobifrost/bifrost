import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/app-sdk/files", () => ({ files: { upload: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { files } from "@/lib/app-sdk/files";
import { useFileUpload } from "./useFileUpload";

describe("useFileUpload", () => {
	beforeEach(() => vi.mocked(files.upload).mockReset());

	it("uploads each file to {prefix}/{name} then fires onUploaded", async () => {
		vi.mocked(files.upload).mockResolvedValue({
			url: "u",
			path: "p",
			expiresIn: 600,
		});
		const onUploaded = vi.fn();
		const { result } = renderHook(() =>
			useFileUpload("gallery", "global", "sub", onUploaded),
		);
		const a = new File(["x"], "a.png", { type: "image/png" });
		const b = new File(["y"], "b.png", { type: "image/png" });
		await act(async () => {
			await result.current.uploadFiles([a, b]);
		});
		expect(files.upload).toHaveBeenCalledWith("sub/a.png", a, {
			location: "gallery",
			scope: "global",
		});
		expect(files.upload).toHaveBeenCalledWith("sub/b.png", b, {
			location: "gallery",
			scope: "global",
		});
		await waitFor(() => expect(onUploaded).toHaveBeenCalled());
	});

	it("is a no-op when location is null (read-only / no folder)", async () => {
		const onUploaded = vi.fn();
		const { result } = renderHook(() =>
			useFileUpload(null, "global", "", onUploaded),
		);
		await act(async () => {
			await result.current.uploadFiles([new File(["x"], "a.png")]);
		});
		expect(files.upload).not.toHaveBeenCalled();
		expect(onUploaded).not.toHaveBeenCalled();
	});

	it("uploads to the bare name at the root (no prefix)", async () => {
		vi.mocked(files.upload).mockResolvedValue({
			url: "u",
			path: "p",
			expiresIn: 600,
		});
		const { result } = renderHook(() =>
			useFileUpload("gallery", null, "", vi.fn()),
		);
		const f = new File(["x"], "root.txt", { type: "text/plain" });
		await act(async () => {
			await result.current.uploadFiles([f]);
		});
		expect(files.upload).toHaveBeenCalledWith("root.txt", f, {
			location: "gallery",
			scope: null,
		});
	});
});
