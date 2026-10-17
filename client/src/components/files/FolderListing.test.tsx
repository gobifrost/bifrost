import {
	renderWithProviders as render,
	screen,
	fireEvent,
	waitFor,
} from "@/test-utils";
import { act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const mockMedia = vi.fn(() => false);
vi.mock("@/hooks/useMediaQuery", () => ({ useMediaQuery: () => mockMedia() }));
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
		mockMedia.mockReturnValue(false);
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

	it("sorts folders first, filters locally, and activates the selected row from the keyboard", async () => {
		vi.mocked(listStructure).mockResolvedValue([
			{ name: "zeta.txt", kind: "file", path: "zeta.txt" },
			{ name: "admin", kind: "folder", path: "admin" },
			{ name: "alpha.txt", kind: "file", path: "alpha.txt" },
		]);
		const onOpenFolder = vi.fn();
		const onSelectFile = vi.fn();
		const { user } = render(
			<FolderListing
				scope={null}
				location="gallery"
				prefix=""
				readOnly={false}
				selectedPath="alpha.txt"
				onOpenFolder={onOpenFolder}
				onSelectFile={onSelectFile}
				onRowAction={vi.fn()}
				onFolderAction={vi.fn()}
				onUploaded={vi.fn()}
			/>,
		);

		const rows = await screen.findAllByRole("button", {
			name: /^(admin|alpha\.txt|zeta\.txt)$/,
		});
		expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual([
			"admin",
			"alpha.txt",
			"zeta.txt",
		]);
		expect(screen.getByRole("listitem", { name: "alpha.txt" })).toHaveClass(
			"tree-row-selected",
		);

		await user.click(screen.getByLabelText("Search this folder"));
		await user.keyboard("zeta");
		expect(screen.queryByRole("button", { name: "admin" })).toBeNull();
		expect(screen.getByText("1 item matching 3 items")).toBeVisible();
		screen.getByRole("button", { name: "zeta.txt" }).focus();
		await user.keyboard("{Enter}");
		expect(onSelectFile).toHaveBeenCalledWith("zeta.txt");
		expect(onOpenFolder).not.toHaveBeenCalled();

		await user.clear(screen.getByLabelText("Search this folder"));
		await user.keyboard("missing");
		expect(screen.getByText(/No matches for "missing"/)).toBeVisible();
		await user.click(screen.getByRole("button", { name: "Clear search" }));
		expect(screen.getByRole("button", { name: "admin" })).toBeVisible();
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

	it("opens overflow actions from the keyboard without activating the row", async () => {
		const onOpenFolder = vi.fn();
		const onFolderAction = vi.fn();
		const { user } = render(
			<FolderListing
				scope={null}
				location="gallery"
				prefix=""
				readOnly={false}
				onOpenFolder={onOpenFolder}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onFolderAction={onFolderAction}
				onUploaded={vi.fn()}
			/>,
		);

		expect(
			await screen.findByRole("listitem", { name: "team" }),
		).toBeVisible();
		screen.getByRole("button", { name: "Actions for team" }).focus();
		await user.keyboard("{Enter}");
		expect(onOpenFolder).not.toHaveBeenCalled();
		await user.click(screen.getByRole("menuitem", { name: "New Policy" }));
		expect(onFolderAction).toHaveBeenCalledWith("newPolicy", "team");
		expect(onOpenFolder).not.toHaveBeenCalled();
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

	it("keeps policy management separate from read-only file data", async () => {
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
		expect(screen.getByText("Manage Policy")).toBeInTheDocument();
		expect(screen.queryByText("Delete")).not.toBeInTheDocument();

		fireEvent.contextMenu(await screen.findByText("team"));
		expect(screen.getByText("New Policy")).toBeInTheDocument();
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
		expect(screen.queryByText(/drag files here/i)).not.toBeInTheDocument();
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

	it("announces the current upload target while a file is in flight", async () => {
		let resolveUpload!: (value: {
			url: string;
			path: string;
			expiresIn: number;
		}) => void;
		const pendingUpload = new Promise<{
			url: string;
			path: string;
			expiresIn: number;
		}>((resolve) => {
			resolveUpload = resolve;
		});
		vi.mocked(files.upload).mockReturnValueOnce(pendingUpload);
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
				onUploaded={vi.fn()}
			/>,
		);
		const section = (await screen.findByText("a.png")).closest("section")!;
		const file = new File(["x"], "live.png", { type: "image/png" });
		fireEvent.drop(section, { dataTransfer: { files: [file] } });
		expect(await screen.findByRole("status")).toHaveTextContent(
			"Uploading live.png to gallery/sub",
		);
		await act(async () => {
			resolveUpload({ url: "u", path: "sub/live.png", expiresIn: 600 });
		});
		await waitFor(() =>
			expect(screen.queryByRole("status")).not.toBeInTheDocument(),
		);
	});

	it("shows upload errors inline and retries only the failed files", async () => {
		vi.mocked(files.upload)
			.mockResolvedValueOnce({
				url: "u",
				path: "sub/a.png",
				expiresIn: 600,
			})
			.mockRejectedValueOnce(new Error("Upload denied"))
			.mockResolvedValueOnce({
				url: "u",
				path: "sub/b.png",
				expiresIn: 600,
			})
			.mockResolvedValueOnce({
				url: "u",
				path: "sub/c.png",
				expiresIn: 600,
			});
		const onUploaded = vi.fn();
		const { user } = render(
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
		const section = (await screen.findByText("a.png")).closest("section")!;
		const first = new File(["x"], "a.png", { type: "image/png" });
		const second = new File(["y"], "b.png", { type: "image/png" });
		const third = new File(["z"], "c.png", { type: "image/png" });
		fireEvent.drop(section, {
			dataTransfer: { files: [first, second, third] },
		});
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Upload to gallery/sub failed at b.png",
		);
		expect(
			screen.getByRole("button", { name: "Retry upload" }),
		).toBeVisible();
		await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1));
		await user.click(screen.getByRole("button", { name: "Retry upload" }));
		await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(2));
		expect(files.upload).toHaveBeenNthCalledWith(3, "sub/b.png", second, {
			location: "gallery",
			scope: null,
		});
		expect(files.upload).toHaveBeenNthCalledWith(4, "sub/c.png", third, {
			location: "gallery",
			scope: null,
		});
	});
	it("exposes folder actions through a mobile menu and opens names natively", async () => {
		mockMedia.mockReturnValue(true);
		const onOpenFolder = vi.fn();
		const onFolderAction = vi.fn();
		const { user } = render(
			<FolderListing
				scope={null}
				location="gallery"
				prefix=""
				readOnly={false}
				onOpenFolder={onOpenFolder}
				onSelectFile={vi.fn()}
				onRowAction={vi.fn()}
				onFolderAction={onFolderAction}
				onUploaded={vi.fn()}
			/>,
		);
		await screen.findByRole("list", { name: "Folders and files" });
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "team" }));
		expect(onOpenFolder).toHaveBeenCalledWith("team");
		await user.click(
			screen.getByRole("button", { name: "Actions for team" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "New Policy" }));
		expect(onFolderAction).toHaveBeenCalledWith("newPolicy", "team");
	});

	it("shows failed folder loads as recoverable errors rather than empty upload targets", async () => {
		vi.mocked(listStructure).mockRejectedValueOnce(
			new Error("Unavailable"),
		);
		const { user } = render(
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
		await screen.findByRole("alert");
		expect(
			screen.queryByRole("button", { name: /Drag files here/ }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Retry folder" }));
		expect(
			await screen.findByRole("button", { name: "team" }),
		).toBeVisible();
	});

	it("reports rejected downloads and retries without losing the listing", async () => {
		vi.mocked(files.download)
			.mockRejectedValueOnce(new Error("Download unavailable"))
			.mockResolvedValueOnce(new Blob(["fixture"]));
		const create = vi.fn(() => "blob:fixture");
		const revoke = vi.fn();
		Object.defineProperty(URL, "createObjectURL", {
			value: create,
			configurable: true,
		});
		Object.defineProperty(URL, "revokeObjectURL", {
			value: revoke,
			configurable: true,
		});
		const anchorClick = vi
			.spyOn(HTMLAnchorElement.prototype, "click")
			.mockImplementation(() => {});
		const { user } = render(
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
		await user.click(
			await screen.findByRole("button", { name: "Actions for a.png" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Download" }));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Download unavailable",
		);
		expect(screen.getByRole("button", { name: "a.png" })).toBeVisible();
		await user.click(
			screen.getByRole("button", { name: "Retry download" }),
		);
		await waitFor(() =>
			expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
		);
		expect(files.download).toHaveBeenLastCalledWith("a.png", {
			location: "gallery",
			scope: null,
		});
		expect(anchorClick).toHaveBeenCalledOnce();
		expect(revoke).toHaveBeenCalledWith("blob:fixture");
		anchorClick.mockRestore();
	});
	it.each([true, false])(
		"keeps read-only data actions out of menus while retaining policy management (compact=%s)",
		async (compact) => {
			mockMedia.mockReturnValue(compact);
			const { user } = render(
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
			await user.click(
				await screen.findByRole("button", {
					name: "Actions for a.png",
				}),
			);
			expect(
				screen.getByRole("menuitem", { name: "Download" }),
			).toBeVisible();
			expect(
				screen.getByRole("menuitem", { name: "Test Access" }),
			).toBeVisible();
			expect(
				screen.queryByRole("menuitem", { name: "Delete" }),
			).not.toBeInTheDocument();
			expect(
				screen.getByRole("menuitem", { name: "Manage Policy" }),
			).toBeVisible();
			await user.keyboard("{Escape}");
			await user.click(
				screen.getByRole("button", { name: "Actions for team" }),
			);
			expect(
				screen.getByRole("menuitem", { name: "Effective Access" }),
			).toBeVisible();
			expect(
				screen.queryByRole("menuitem", { name: "Upload" }),
			).not.toBeInTheDocument();
			expect(
				screen.getByRole("menuitem", { name: "New Policy" }),
			).toBeVisible();
		},
	);
});
