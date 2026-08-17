/**
 * Tests for the builder revision list — Source/Preview badge rendering, the
 * undo confirmation flow, and the states that disable Undo.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { RevisionList, formatBytes } from "./RevisionList";
import type { BuilderRevision } from "@/services/builder";

function revision(overrides: Partial<BuilderRevision> = {}): BuilderRevision {
	return {
		id: "rev-1",
		parent_revision_id: null,
		restored_from_revision_id: null,
		source_sha256: "abc",
		size_bytes: 2048,
		summary: "Add expense table",
		created_at: "2026-07-25T10:00:00Z",
		created_by: "user-1",
		is_current: false,
		is_deployed: false,
		...overrides,
	};
}

const onUndo = vi.fn();
const onDownload = vi.fn();

function renderList(props: Partial<Parameters<typeof RevisionList>[0]> = {}) {
	return renderWithProviders(
		<RevisionList
			revisions={[revision()]}
			isLoading={false}
			canUndo
			undoingRevisionId={null}
			onUndo={onUndo}
			onDownload={onDownload}
			{...props}
		/>,
	);
}

beforeEach(() => {
	onUndo.mockReset();
	onDownload.mockReset();
});

describe("RevisionList states", () => {
	it("shows a skeleton while loading", () => {
		renderList({ isLoading: true });

		expect(screen.getByTestId("revisions-loading")).toBeInTheDocument();
		expect(screen.queryByTestId("revision-list")).not.toBeInTheDocument();
	});

	it("shows an empty state when there are no revisions", () => {
		renderList({ revisions: [] });

		expect(screen.getByText(/no revisions yet/i)).toBeInTheDocument();
	});

	it("falls back to a placeholder title when a revision has no summary", () => {
		renderList({ revisions: [revision({ summary: null })] });

		expect(screen.getByText("Untitled revision")).toBeInTheDocument();
	});
});

describe("RevisionList badges", () => {
	it("marks the current revision Source and the deployed revision Preview", () => {
		renderList({
			revisions: [
				revision({ id: "rev-3", is_current: true, summary: "Latest" }),
				revision({ id: "rev-2", is_deployed: true, summary: "Deployed" }),
			],
		});

		const current = screen.getByTestId("revision-rev-3");
		expect(within(current).getByText("Source")).toBeInTheDocument();
		expect(within(current).queryByText("Preview")).not.toBeInTheDocument();

		const deployed = screen.getByTestId("revision-rev-2");
		expect(within(deployed).getByText("Preview")).toBeInTheDocument();
		expect(within(deployed).queryByText("Source")).not.toBeInTheDocument();
	});

	it("shows both badges when the current revision is also deployed", () => {
		renderList({
			revisions: [revision({ id: "rev-1", is_current: true, is_deployed: true })],
		});

		const row = screen.getByTestId("revision-rev-1");
		expect(within(row).getByText("Source")).toBeInTheDocument();
		expect(within(row).getByText("Preview")).toBeInTheDocument();
	});

	it("marks a restored revision", () => {
		renderList({
			revisions: [revision({ restored_from_revision_id: "rev-0" })],
		});

		expect(screen.getByText("Restored")).toBeInTheDocument();
	});
});

describe("undo flow", () => {
	it("requires confirmation before calling onUndo", async () => {
		const { user } = renderList({
			revisions: [revision({ id: "rev-2", summary: "Earlier work" })],
		});

		await user.click(
			screen.getByRole("button", { name: /undo to revision rev-2/i }),
		);

		expect(onUndo).not.toHaveBeenCalled();
		const dialog = screen.getByRole("alertdialog");
		expect(
			within(dialog).getByText(/restore this revision\?/i),
		).toBeInTheDocument();
		expect(within(dialog).getByText(/earlier work/i)).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /^restore$/i }));

		expect(onUndo).toHaveBeenCalledWith("rev-2");
	});

	it("does not call onUndo when the confirmation is cancelled", async () => {
		const { user } = renderList({ revisions: [revision({ id: "rev-2" })] });

		await user.click(
			screen.getByRole("button", { name: /undo to revision rev-2/i }),
		);
		await user.click(screen.getByRole("button", { name: /cancel/i }));

		expect(onUndo).not.toHaveBeenCalled();
	});

	it("disables undo on the revision that is already current", () => {
		renderList({ revisions: [revision({ id: "rev-1", is_current: true })] });

		expect(
			screen.getByRole("button", { name: /undo to revision rev-1/i }),
		).toBeDisabled();
	});

	it("disables undo when there is no builder session", () => {
		renderList({ canUndo: false });

		expect(
			screen.getByRole("button", { name: /undo to revision rev-1/i }),
		).toBeDisabled();
	});

	it("disables undo on every row while an undo is in flight", () => {
		renderList({
			revisions: [revision({ id: "rev-2" }), revision({ id: "rev-3" })],
			undoingRevisionId: "rev-2",
		});

		expect(
			screen.getByRole("button", { name: /undo to revision rev-2/i }),
		).toBeDisabled();
		expect(
			screen.getByRole("button", { name: /undo to revision rev-3/i }),
		).toBeDisabled();
	});
});

describe("download", () => {
	it("calls onDownload with the revision id", async () => {
		const { user } = renderList({ revisions: [revision({ id: "rev-7" })] });

		await user.click(
			screen.getByRole("button", { name: /download revision rev-7/i }),
		);

		expect(onDownload).toHaveBeenCalledWith("rev-7");
	});
});

describe("formatBytes", () => {
	it("scales through bytes, kilobytes, and megabytes", () => {
		expect(formatBytes(512)).toBe("512 B");
		expect(formatBytes(2048)).toBe("2.0 KB");
		expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
	});
});
