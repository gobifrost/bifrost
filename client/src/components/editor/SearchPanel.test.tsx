import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { useEditorStore } from "@/stores/editorStore";
import { fileService } from "@/services/fileService";
import { SearchPanel } from "./SearchPanel";
import { SearchResultItem } from "./SearchResultItem";
import {
	searchService,
	type SearchMatch,
	type SearchResponse,
} from "@/services/searchService";
vi.mock("@/services/searchService", () => ({
	searchService: { searchFiles: vi.fn() },
}));
vi.mock("@/services/fileService", () => ({
	fileService: { readFile: vi.fn() },
}));
const workspace = { kind: "workspace" as const, editable: true };
const result: SearchMatch = {
	file_path: "folder/long_file.py",
	source: workspace,
	line: 8,
	column: 0,
	text: "items[0] = items[1]",
	context_before: [],
	context_after: [],
};
const page = (overrides: Partial<SearchResponse> = {}): SearchResponse => ({
	query: "items",
	output_mode: "content",
	matches: [result],
	files: [],
	returned: 1,
	has_more_matches: false,
	response_complete: true,
	next_cursor: null,
	guidance: "Complete.",
	search_time_ms: 1,
	...overrides,
});
const response = page();
beforeEach(() => {
	vi.resetAllMocks();
	useEditorStore.setState({
		tabs: [],
		activeTabIndex: -1,
		pendingLineReveal: null,
	});
});

it("highlights literal regex characters without interpreting them", () => {
	const { container } = render(
		<SearchResultItem
			result={result}
			query="["
			caseSensitive={false}
			useRegex={false}
			onClick={vi.fn()}
		/>,
	);
	expect(container.querySelectorAll("mark")).toHaveLength(2);
	expect(screen.getByRole("button")).toHaveTextContent(result.text);
});

it("keeps result labels tied to the submitted query and exposes retry without discarding them", async () => {
	vi.mocked(searchService.searchFiles)
		.mockResolvedValueOnce(response)
		.mockRejectedValueOnce(new Error("Search temporarily unavailable"))
		.mockResolvedValueOnce(page({ matches: [], returned: 0 }));
	const user = userEvent.setup();
	render(<SearchPanel />);
	const input = screen.getByRole("textbox", { name: "Search file contents" });
	await user.type(input, "items{Enter}");
	await screen.findByText(result.file_path);
	expect(screen.getByRole("button", { name: "Match case" })).toHaveAttribute(
		"aria-pressed",
		"false",
	);
	await user.clear(input);
	await user.type(input, "other");
	expect(screen.getByRole("status")).toHaveTextContent("matches for “items”");
	await user.keyboard("{Enter}");
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Search temporarily unavailable",
	);
	expect(screen.getByText(result.file_path)).toBeVisible();
	await user.click(screen.getByRole("button", { name: "Retry search" }));
	expect(await screen.findByText(/No matches found/)).toBeVisible();
});

it("clears an in-flight search without allowing its late response to restore results", async () => {
	let finish!: (value: SearchResponse) => void;
	vi.mocked(searchService.searchFiles).mockImplementation(
		() =>
			new Promise((resolve) => {
				finish = resolve;
			}),
	);
	const user = userEvent.setup();
	render(<SearchPanel />);
	await user.type(screen.getByRole("textbox"), "items{Enter}");
	await user.click(screen.getByRole("button", { name: "Clear search" }));
	await act(async () => {
		finish(response);
	});
	expect(
		screen.queryByRole("region", { name: "Search results" }),
	).not.toBeInTheDocument();
	expect(screen.getByRole("textbox")).toHaveValue("");
});

it("opens an existing dirty buffer at the matching line without fetching or overwriting it", async () => {
	const store = useEditorStore.getState();
	store.openFileInTab(
		{
			name: "long_file.py",
			path: result.file_path,
			type: "file",
			size: 10,
			modified: "2026-09-06T00:00:00Z",
			extension: ".py",
			entity_type: null,
			entity_id: null,
		},
		"server content",
		"utf-8",
		"etag",
	);
	store.setFileContent("unsaved draft");
	vi.mocked(searchService.searchFiles).mockResolvedValue(response);
	const onResultOpened = vi.fn();
	const user = userEvent.setup();
	render(<SearchPanel onResultOpened={onResultOpened} />);
	await user.type(screen.getByRole("textbox"), "items{Enter}");
	await user.click(
		await screen.findByRole("button", { name: /folder\/long_file.py/ }),
	);
	expect(fileService.readFile).not.toHaveBeenCalled();
	expect(useEditorStore.getState().tabs[0].content).toBe("unsaved draft");
	expect(useEditorStore.getState().tabs[0].unsavedChanges).toBe(true);
	expect(useEditorStore.getState().pendingLineReveal).toBe(8);
	expect(onResultOpened).toHaveBeenCalledTimes(1);
});

it("retains results after an open failure and retries the same file with its etag and target line", async () => {
	vi.mocked(searchService.searchFiles).mockResolvedValue(response);
	vi.mocked(fileService.readFile)
		.mockRejectedValueOnce(new Error("Unavailable"))
		.mockResolvedValueOnce({
			content: "loaded",
			path: result.file_path,
			content_modified: false,
			needs_indexing: false,
			encoding: "utf-8",
			etag: "server-etag",
			size: 6,
			modified: "2026-09-06T00:00:00Z",
		});
	const user = userEvent.setup();
	render(<SearchPanel />);
	await user.type(screen.getByRole("textbox"), "items{Enter}");
	await user.click(
		await screen.findByRole("button", { name: /folder\/long_file.py/ }),
	);
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Couldn’t open folder/long_file.py",
	);
	await user.click(
		screen.getByRole("button", { name: "Retry opening file" }),
	);
	expect(useEditorStore.getState().tabs[0].etag).toBe("server-etag");
	expect(useEditorStore.getState().pendingLineReveal).toBe(8);
});

it("loads the next page with the returned cursor and appends results", async () => {
	const second: SearchMatch = {
		...result,
		file_path: "folder/b.py",
		line: 2,
	};
	vi.mocked(searchService.searchFiles)
		.mockResolvedValueOnce(
			page({
				has_more_matches: true,
				response_complete: false,
				next_cursor: "c1",
			}),
		)
		.mockResolvedValueOnce(page({ matches: [second] }));
	const user = userEvent.setup();
	render(<SearchPanel />);
	await user.type(screen.getByRole("textbox"), "items{Enter}");
	expect(await screen.findByRole("status")).toHaveTextContent(
		"1+ matches for “items”",
	);
	await user.click(screen.getByRole("button", { name: "Load more results" }));
	expect(vi.mocked(searchService.searchFiles)).toHaveBeenLastCalledWith(
		expect.objectContaining({ query: "items", cursor: "c1", limit: 100 }),
	);
	expect(await screen.findByText("folder/b.py")).toBeVisible();
	expect(screen.getByText(result.file_path)).toBeVisible();
	expect(
		screen.queryByRole("button", { name: "Load more results" }),
	).not.toBeInTheDocument();
	expect(screen.getByRole("status")).toHaveTextContent(
		"2 matches for “items”",
	);
});

it("labels Solution hits read-only and does not open them as workspace files", async () => {
	const solutionHit: SearchMatch = {
		...result,
		file_path: "functions/sync.py",
		source: {
			kind: "solution",
			solution_slug: "covi-psa",
			editable: false,
		},
	};
	vi.mocked(searchService.searchFiles).mockResolvedValue(
		page({ matches: [solutionHit] }),
	);
	const user = userEvent.setup();
	render(<SearchPanel />);
	await user.type(screen.getByRole("textbox"), "items{Enter}");
	const button = await screen.findByRole("button", {
		name: /functions\/sync.py/,
	});
	expect(button).toHaveTextContent("Solution · covi-psa · read-only");
	expect(button).toBeDisabled();
	expect(fileService.readFile).not.toHaveBeenCalled();
});
