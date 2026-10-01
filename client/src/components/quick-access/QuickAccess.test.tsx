/**
 * Tests for QuickAccess (Cmd+K palette), rebuilt on CommandDialog.
 *
 * Covers:
 *   - Closed: renders nothing
 *   - Open with empty query: idle empty state + hints
 *   - Debounced search across forms / workflows / scripts, server order kept
 *   - "No results found" state
 *   - Selecting a form result navigates and closes
 *   - Escape closes via onClose
 *   - Footer hints row appears only with results
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";

import { renderWithProviders, screen } from "@/test-utils";

const navigateMock = vi.fn();

vi.mock("react-router-dom", async (importOriginal) => {
	const actual = await importOriginal<typeof import("react-router-dom")>();
	return {
		...actual,
		useNavigate: () => navigateMock,
	};
});

const editorStore = {
	openFileInTab: vi.fn(),
	openEditor: vi.fn(),
	setSidebarPanel: vi.fn(),
};

vi.mock("@/stores/editorStore", () => ({
	useEditorStore: (selector: (s: typeof editorStore) => unknown) =>
		selector(editorStore),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true }),
}));

vi.mock("framer-motion", () => ({
	useReducedMotion: () => true,
}));

const formsRef: { data: Array<Record<string, unknown>> } = { data: [] };
vi.mock("@/hooks/useForms", () => ({
	useForms: () => ({ data: formsRef.data }),
}));

const workflowsRef: { data: { workflows: Array<Record<string, unknown>> } } = {
	data: { workflows: [] },
};
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => ({ data: workflowsRef.data }),
}));

const searchFilesMock = vi.fn();
vi.mock("@/services/searchService", () => ({
	searchService: {
		searchFiles: (...args: unknown[]) => searchFilesMock(...args),
	},
}));

const readFileMock = vi.fn();
vi.mock("@/services/fileService", () => ({
	fileService: {
		readFile: (...args: unknown[]) => readFileMock(...args),
	},
}));

import { QuickAccess } from "./QuickAccess";

beforeEach(() => {
	vi.clearAllMocks();
	formsRef.data = [
		{ id: "f-1", name: "Onboard User", description: "HR onboarding" },
	];
	workflowsRef.data = {
		workflows: [
			{ name: "onboard_workflow", description: "Runs onboarding" },
		],
	};
	searchFilesMock.mockResolvedValue({
		files: [
			{
				file_path: "workflows/onboard.py",
				match_count: 3,
				first_line: 12,
			},
		],
	});
});

async function typeQuery(value: string) {
	const input = screen.getByPlaceholderText(
		"Search forms, workflows, and scripts...",
	);
	fireEvent.change(input, { target: { value } });
	// Wait out the 300ms debounce + async search.
	await screen.findByText("Onboard User", undefined, { timeout: 3000 });
}

describe("QuickAccess — visibility", () => {
	it("renders nothing when closed", () => {
		renderWithProviders(<QuickAccess isOpen={false} onClose={vi.fn()} />);
		expect(
			screen.queryByPlaceholderText(
				"Search forms, workflows, and scripts...",
			),
		).not.toBeInTheDocument();
	});

	it("shows the idle empty state when open with no query", () => {
		renderWithProviders(<QuickAccess isOpen onClose={vi.fn()} />);
		expect(
			screen.getByText("Search for forms, workflows, and scripts"),
		).toBeInTheDocument();
		// No footer hints without results
		expect(screen.queryByText("↑↓ to navigate")).not.toBeInTheDocument();
	});
});

describe("QuickAccess — searching", () => {
	it("shows all three result types after the debounce, in server order", async () => {
		renderWithProviders(<QuickAccess isOpen onClose={vi.fn()} />);
		await typeQuery("onboard");

		expect(screen.getByText("Onboard User")).toBeInTheDocument();
		expect(screen.getByText("onboard_workflow")).toBeInTheDocument();
		expect(screen.getByText("onboard.py")).toBeInTheDocument();
		// Descriptions render
		expect(screen.getByText("HR onboarding")).toBeInTheDocument();
		expect(
			screen.getByText("3 matches, first at line 12"),
		).toBeInTheDocument();
		// Only editable workspace source is offered, one entry per file
		expect(searchFilesMock).toHaveBeenCalledWith(
			expect.objectContaining({
				query: "onboard",
				source: "workspace",
				output_mode: "files",
				limit: 20,
			}),
		);
		// Forms come before workflows before scripts (server-ranked order kept)
		const items = screen.getAllByRole("option");
		expect(items[0]).toHaveTextContent("Onboard User");
		expect(items[1]).toHaveTextContent("onboard_workflow");
		expect(items[2]).toHaveTextContent("onboard.py");
		// Footer hints appear with results
		expect(screen.getByText("↑↓ to navigate")).toBeInTheDocument();
		expect(screen.getByText("Enter to select")).toBeInTheDocument();
		expect(screen.getByText("Esc to close")).toBeInTheDocument();
	});

	it("shows the no-results state for a query with no matches", async () => {
		formsRef.data = [];
		workflowsRef.data = { workflows: [] };
		searchFilesMock.mockResolvedValue({ files: [] });

		renderWithProviders(<QuickAccess isOpen onClose={vi.fn()} />);
		const input = screen.getByPlaceholderText(
			"Search forms, workflows, and scripts...",
		);
		fireEvent.change(input, { target: { value: "zzz-nothing" } });

		await screen.findByText("No results found", undefined, {
			timeout: 3000,
		});
	});

	it("surfaces a script-search error and retries the query", async () => {
		searchFilesMock
			.mockRejectedValueOnce(new Error("offline"))
			.mockResolvedValueOnce({
				files: [
					{
						file_path: "workflows/onboard.py",
						match_count: 3,
						first_line: 12,
					},
				],
			});

		renderWithProviders(<QuickAccess isOpen onClose={vi.fn()} />);
		await typeQuery("onboard");

		expect(
			await screen.findByText(/file search is temporarily unavailable/i),
		).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /retry/i })).toHaveClass(
			"min-h-11",
		);

		fireEvent.click(screen.getByRole("button", { name: /retry/i }));

		await waitFor(() =>
			expect(screen.getByText("onboard.py")).toBeInTheDocument(),
		);
	});
});

describe("QuickAccess — selection and close", () => {
	it("navigates to the form and closes when a form result is selected", async () => {
		const onClose = vi.fn();
		renderWithProviders(<QuickAccess isOpen onClose={onClose} />);
		await typeQuery("onboard");

		fireEvent.click(screen.getByText("Onboard User"));

		await waitFor(() => {
			expect(navigateMock).toHaveBeenCalledWith("/execute/f-1");
		});
		expect(onClose).toHaveBeenCalled();
	});

	it("navigates to the workflow execute page when a workflow is selected", async () => {
		const onClose = vi.fn();
		renderWithProviders(<QuickAccess isOpen onClose={onClose} />);
		await typeQuery("onboard");

		fireEvent.click(screen.getByText("onboard_workflow"));

		await waitFor(() => {
			expect(navigateMock).toHaveBeenCalledWith(
				"/workflows/onboard_workflow/execute",
			);
		});
		expect(onClose).toHaveBeenCalled();
	});

	it("opens a script in the editor when a script result is selected", async () => {
		const onClose = vi.fn();
		readFileMock.mockResolvedValue({
			content: "def onboard(): pass",
			encoding: "utf-8",
		});
		renderWithProviders(<QuickAccess isOpen onClose={onClose} />);
		await typeQuery("onboard");

		fireEvent.click(screen.getByText("onboard.py"));

		await waitFor(() => {
			expect(readFileMock).toHaveBeenCalledWith("workflows/onboard.py");
		});
		expect(editorStore.openEditor).toHaveBeenCalled();
		expect(editorStore.openFileInTab).toHaveBeenCalled();
		// Python file → Run panel
		expect(editorStore.setSidebarPanel).toHaveBeenCalledWith("run");
		expect(onClose).toHaveBeenCalled();
	});

	it("calls onClose when Escape is pressed", async () => {
		const onClose = vi.fn();
		renderWithProviders(<QuickAccess isOpen onClose={onClose} />);

		fireEvent.keyDown(
			screen.getByPlaceholderText(
				"Search forms, workflows, and scripts...",
			),
			{ key: "Escape" },
		);

		await waitFor(() => {
			expect(onClose).toHaveBeenCalled();
		});
	});
});
