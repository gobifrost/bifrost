import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/fileStructure", () => ({ listStructure: vi.fn() }));
vi.mock("@/lib/app-sdk/files", () => ({
	files: { upload: vi.fn(), download: vi.fn(), delete: vi.fn() },
}));
vi.mock("@/components/solutions/SolutionManagedBadge", () => ({
	SolutionManagedBadge: () => (
		<span data-testid="solution-managed-badge">Managed</span>
	),
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
				onFolderAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);
		expect(await screen.findByText("team")).toBeInTheDocument();
		expect(screen.getByText("a.png")).toBeInTheDocument();
		fireEvent.click(screen.getByText("team"));
		expect(onOpenFolder).toHaveBeenCalledWith("team");
	});

	it("opens a folder context menu with folder actions", async () => {
		const onFolderAction = vi.fn();
		render(
			<FolderListing
				scope={null}
				location="gallery"
				prefix=""
				readOnly={false}
				onOpenFolder={vi.fn()}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onFolderAction={onFolderAction}
				onUploaded={vi.fn()}
			/>,
		);
		fireEvent.contextMenu(await screen.findByText("team"));
		fireEvent.click(await screen.findByText("New Policy"));
		expect(onFolderAction).toHaveBeenCalledWith("newPolicy", "team");
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
				onFolderAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);
		await screen.findByText("a.png");
		expect(
			screen.queryByRole("button", { name: /upload/i }),
		).not.toBeInTheDocument();
	});

	it("hides policy and delete mutation actions when read-only", async () => {
		const onRowAction = vi.fn();
		const onFolderAction = vi.fn();
		render(
			<FolderListing
				scope={null}
				location="uploads"
				prefix=""
				readOnly
				onOpenFolder={vi.fn()}
				onSelectFile={vi.fn()}
				onRowAction={onRowAction}
				onFolderAction={onFolderAction}
				onUploaded={vi.fn()}
			/>,
		);

		fireEvent.contextMenu(await screen.findByText("a.png"));
		expect(screen.queryByText("Manage Policy")).not.toBeInTheDocument();
		expect(screen.queryByText("Delete")).not.toBeInTheDocument();

		fireEvent.contextMenu(await screen.findByText("team"));
		expect(screen.queryByText("New Policy")).not.toBeInTheDocument();
		expect(screen.queryByText("Upload")).not.toBeInTheDocument();
	});

	it("shows managed lock badges for solution-owned rows", async () => {
		render(
			<FolderListing
				scope="sol-1"
				location="reports"
				prefix=""
				readOnly
				managedBySolution
				solutionId="sol-1"
				onOpenFolder={vi.fn()}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onFolderAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);

		await screen.findByText("a.png");
		expect(screen.getAllByTestId("solution-managed-badge")).toHaveLength(2);
	});

	it("shows a click-to-upload dropzone for an empty writable folder", async () => {
		vi.mocked(listStructure).mockResolvedValue([]);
		render(
			<FolderListing
				scope={null}
				location="gallery"
				prefix=""
				readOnly={false}
				onOpenFolder={vi.fn()}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onFolderAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);
		expect(
			await screen.findByText(/drag files here or click to upload/i),
		).toBeInTheDocument();
	});

	it("shows a plain empty state (no dropzone) for an empty read-only folder", async () => {
		vi.mocked(listStructure).mockResolvedValue([]);
		render(
			<FolderListing
				scope={null}
				location="uploads"
				prefix=""
				readOnly
				onOpenFolder={vi.fn()}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onFolderAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);
		expect(await screen.findByText(/no files here/i)).toBeInTheDocument();
		expect(
			screen.queryByText(/drag files here/i),
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
				onFolderAction={vi.fn()}
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
