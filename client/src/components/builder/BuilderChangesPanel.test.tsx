import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockGetRevisionDiff = vi.fn();

vi.mock("@/services/builder", async () => {
	const actual = await vi.importActual<typeof import("@/services/builder")>(
		"@/services/builder",
	);
	return {
		...actual,
		getRevisionDiff: (...args: unknown[]) => mockGetRevisionDiff(...args),
	};
});

import { BuilderChangesPanel } from "./BuilderChangesPanel";

const revision = {
	id: "revision-1",
	parent_revision_id: null,
	restored_from_revision_id: null,
	source_sha256: "abc",
	size_bytes: 120,
	summary: "Initial scaffold",
	created_at: "2026-07-28T12:00:00Z",
	created_by: "user-1",
	is_current: true,
	is_deployed: false,
};

function renderPanel() {
	return renderWithProviders(
		<BuilderChangesPanel
			solutionId="solution-1"
			revisions={[revision]}
			isLoading={false}
			canUndo
			undoingRevisionId={null}
			onUndo={vi.fn()}
			onDownload={vi.fn()}
		/>,
	);
}

beforeEach(() => {
	mockGetRevisionDiff.mockReset();
	mockGetRevisionDiff.mockResolvedValue({
		revision_id: "revision-1",
		against_revision_id: null,
		total: 1,
		additions: 1,
		deletions: 0,
		files: [
			{
				path: "apps/demo/src/App.tsx",
				status: "added",
				additions: 1,
				deletions: 0,
				is_binary: false,
				diff: "+++ b/apps/demo/src/App.tsx\n+export default App",
				truncated: false,
			},
		],
	});
});

describe("BuilderChangesPanel", () => {
	it("shows revision evidence and the selected file diff", async () => {
		renderPanel();

		expect(await screen.findByText("1 files")).toBeInTheDocument();
		expect(screen.getByText("apps/demo/src/App.tsx")).toBeInTheDocument();
		expect(screen.getByText("+export default App")).toBeInTheDocument();
	});

	it("offers a retry when the comparison fails", async () => {
		mockGetRevisionDiff.mockRejectedValue(new Error("Diff timed out"));
		const { user } = renderPanel();

		expect(await screen.findByText("Diff timed out")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Try again" }));
		await waitFor(() => {
			expect(mockGetRevisionDiff).toHaveBeenCalledTimes(2);
		});
	});
});
