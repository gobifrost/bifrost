import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/app-sdk/files", () => ({
	files: {
		read: vi.fn(),
		signedUrl: vi.fn(),
		download: vi.fn(),
	},
}));
import { files } from "@/lib/app-sdk/files";
import { FilePreview } from "./FilePreview";

describe("FilePreview", () => {
	beforeEach(() => {
		vi.mocked(files.read).mockReset();
		vi.mocked(files.signedUrl).mockReset();
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

	it("renders an image for an image file", async () => {
		vi.mocked(files.signedUrl).mockResolvedValue({
			url: "https://example/img.png",
			path: "p",
			expiresIn: 600,
		});
		render(<FilePreview location="gallery" scope={null} path="pic.png" />);
		await waitFor(() => {
			const img = screen.getByRole("img");
			expect(img).toHaveAttribute("src", "https://example/img.png");
		});
	});

	it("shows download-only for unknown types", () => {
		render(<FilePreview location="gallery" scope={null} path="archive.zip" />);
		expect(screen.getByText(/preview unavailable/i)).toBeInTheDocument();
	});
});
