import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/app-sdk/files", () => ({
	files: {
		read: vi.fn(),
		readBytes: vi.fn(),
		signedUrl: vi.fn(),
		download: vi.fn(),
	},
}));
import { files } from "@/lib/app-sdk/files";
import { FilePreview } from "./FilePreview";

beforeEach(() => {
	// jsdom lacks createObjectURL.
	globalThis.URL.createObjectURL = vi.fn(() => "blob:mock");
	globalThis.URL.revokeObjectURL = vi.fn();
});

describe("FilePreview", () => {
	beforeEach(() => {
		vi.mocked(files.read).mockReset();
		vi.mocked(files.readBytes).mockReset();
	});

	it("prompts to select when no path", () => {
		render(<FilePreview location="gallery" scope={null} path={null} />);
		expect(screen.getByText(/select a file/i)).toBeInTheDocument();
	});

	it("renders text content for a text file", async () => {
		vi.mocked(files.read).mockResolvedValue("hello world");
		render(<FilePreview location="gallery" scope={null} path="notes.txt" />);
		await waitFor(() =>
			expect(screen.getByText("hello world")).toBeInTheDocument(),
		);
		expect(files.read).toHaveBeenCalledWith("notes.txt", {
			location: "gallery",
			scope: null,
		});
	});

	it("renders an image from authenticated bytes (blob url)", async () => {
		vi.mocked(files.readBytes).mockResolvedValue(new Uint8Array([1, 2, 3]));
		render(<FilePreview location="gallery" scope={null} path="pic.png" />);
		await waitFor(() => {
			const img = screen.getByRole("img");
			expect(img).toHaveAttribute("src", "blob:mock");
		});
		expect(files.readBytes).toHaveBeenCalledWith("pic.png", {
			location: "gallery",
			scope: null,
		});
	});

	it("shows a friendly error (not raw 'Forbidden') when a read fails", async () => {
		vi.mocked(files.read).mockRejectedValue(new Error("Forbidden"));
		render(<FilePreview location="gallery" scope={null} path="secret.txt" />);
		await waitFor(() =>
			expect(screen.getByText(/couldn’t load this file/i)).toBeInTheDocument(),
		);
		expect(screen.queryByText(/^Forbidden$/)).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /download instead/i }),
		).toBeInTheDocument();
	});

	it("offers download for types with no inline preview", () => {
		render(<FilePreview location="gallery" scope={null} path="archive.zip" />);
		expect(
			screen.getByText(/no inline preview for this file type/i),
		).toBeInTheDocument();
	});
});
