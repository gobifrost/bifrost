import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/fileStructure", () => ({ listStructure: vi.fn() }));
vi.mock("@/lib/app-sdk/files", () => ({
	files: { upload: vi.fn(), download: vi.fn(), delete: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { listStructure } from "@/services/fileStructure";
import { files } from "@/lib/app-sdk/files";
import { FolderListing } from "./FolderListing";

describe("FolderListing", () => {
	beforeEach(() => {
		vi.mocked(listStructure).mockResolvedValue([
			{ name: "team", kind: "folder", path: "team" },
			{ name: "a.png", kind: "file", path: "a.png" },
		]);
		vi.mocked(files.upload).mockReset();
	});

	it("renders folders and files; opens a folder on click", async () => {
		const onOpenFolder = vi.fn();
		render(
			<FolderListing
				scope={null}
				location="gallery"
				prefix=""
				readOnly={false}
				onOpenFolder={onOpenFolder}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);
		expect(await screen.findByText("team")).toBeInTheDocument();
		expect(screen.getByText("a.png")).toBeInTheDocument();
		fireEvent.click(screen.getByText("team"));
		expect(onOpenFolder).toHaveBeenCalledWith("team");
	});

	it("hides the upload button when read-only", async () => {
		render(
			<FolderListing
				scope={null}
				location="uploads"
				prefix=""
				readOnly
				onOpenFolder={vi.fn()}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);
		await screen.findByText("a.png");
		expect(
			screen.queryByRole("button", { name: /upload/i }),
		).not.toBeInTheDocument();
	});

	it("uploads a dropped file via files.upload then fires onUploaded", async () => {
		vi.mocked(files.upload).mockResolvedValue({
			url: "u",
			path: "a.png",
			expiresIn: 600,
		});
		const onUploaded = vi.fn();
		render(
			<FolderListing
				scope={null}
				location="gallery"
				prefix="sub"
				readOnly={false}
				onOpenFolder={vi.fn()}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onUploaded={onUploaded}
			/>,
		);
		await screen.findByText("a.png");
		const section = screen.getByText("a.png").closest("section")!;
		const file = new File(["x"], "b.png", { type: "image/png" });
		fireEvent.drop(section, { dataTransfer: { files: [file] } });
		await waitFor(() =>
			expect(files.upload).toHaveBeenCalledWith("sub/b.png", file, {
				location: "gallery",
				scope: null,
			}),
		);
		await waitFor(() => expect(onUploaded).toHaveBeenCalled());
	});
});
