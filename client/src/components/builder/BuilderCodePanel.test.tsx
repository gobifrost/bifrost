import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockListRevisionFiles = vi.fn();
const mockGetRevisionFile = vi.fn();

vi.mock("@/services/builder", async () => {
	const actual = await vi.importActual<typeof import("@/services/builder")>(
		"@/services/builder",
	);
	return {
		...actual,
		listRevisionFiles: (...args: unknown[]) => mockListRevisionFiles(...args),
		getRevisionFile: (...args: unknown[]) => mockGetRevisionFile(...args),
	};
});

import { BuilderCodePanel } from "./BuilderCodePanel";

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

beforeEach(() => {
	mockListRevisionFiles.mockReset();
	mockGetRevisionFile.mockReset();
	mockListRevisionFiles.mockResolvedValue([
		{ path: "apps/demo/src/App.tsx", size_bytes: 20, is_text: true },
	]);
	mockGetRevisionFile.mockResolvedValue({
		revision_id: "revision-1",
		path: "apps/demo/src/App.tsx",
		size_bytes: 20,
		encoding: "utf-8",
		content: "export default App",
		truncated: false,
	});
});

describe("BuilderCodePanel", () => {
	it("shows the source tree and selected file content", async () => {
		renderWithProviders(
			<BuilderCodePanel solutionId="solution-1" revision={revision} />,
		);

		expect(
			await screen.findAllByText("apps/demo/src/App.tsx"),
		).not.toHaveLength(0);
		expect(await screen.findByTestId("builder-code-content")).toHaveTextContent(
			"export default App",
		);
	});

	it("offers a retry when the source tree cannot be loaded", async () => {
		mockListRevisionFiles.mockRejectedValue(new Error("Source timed out"));
		const { user } = renderWithProviders(
			<BuilderCodePanel solutionId="solution-1" revision={revision} />,
		);

		expect(await screen.findByText("Source timed out")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Try again" }));
		await waitFor(() => {
			expect(mockListRevisionFiles).toHaveBeenCalledTimes(2);
		});
	});
});
