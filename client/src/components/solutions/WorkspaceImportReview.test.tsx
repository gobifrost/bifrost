import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { WorkspaceImportReview } from "./WorkspaceImportReview";

const preview = {
	preview_token: "preview-1",
	package_name: "Customer operations",
	package_sha256: "a".repeat(64),
	source_kind: "zip",
	items: [
		{
			id: "entity:workflow:alpha",
			kind: "workflow",
			name: "Sink Alpha",
			classification: "conflict",
			match_key: "workflows/sink_alpha.py :: sink_alpha",
			target_id: "destination-alpha-id",
			group_key: "file:workflows/sink_alpha.py",
			scope_change: true,
			diff: [],
		},
		{
			id: "file:workflows/sink_alpha.py",
			kind: "file",
			name: "workflows/sink_alpha.py",
			classification: "conflict",
			match_key: "workflows/sink_alpha.py",
			group_key: "file:workflows/sink_alpha.py",
			diff: [],
		},
		{
			id: "entity:app:customer",
			kind: "app",
			name: "Customer dashboard",
			classification: "conflict",
			match_key: "customer",
			target_id: "destination-app-id",
			group_key: "app:customer",
			diff: [],
		},
		{
			id: "file:modules/customer.py",
			kind: "file",
			name: "modules/customer.py",
			classification: "create",
			group_key: null,
			diff: [],
		},
	],
} as never;

describe("WorkspaceImportReview", () => {
	it("states the compatibility warning simply", () => {
		renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={vi.fn()} />,
		);

		expect(
			screen.getByText(/Keep and Replace decisions can affect other workspace content\./),
		).toBeInTheDocument();
		expect(screen.queryByText(/Package notices/)).toBeNull();
	});

	it("explains what replacing a table changes", () => {
		const tablePreview = {
			preview_token: "preview-1",
			package_name: "Customer operations",
			package_sha256: "a".repeat(64),
			source_kind: "zip",
			items: [{
				id: "entity:table:items", kind: "table", name: "Items",
				classification: "conflict", match_key: "Items", diff: [],
			}],
		} as never;
		renderWithProviders(
			<WorkspaceImportReview preview={tablePreview} decisions={{}} onDecisionsChange={vi.fn()} />,
		);
		expect(screen.getByText(/Replace updates the table definition and access policies; existing rows stay\./)).toBeInTheDocument();
	});

	it("renders one row per definition with its files, in a standard table", async () => {
		renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={vi.fn()} />,
		);

		const table = screen.getByTestId("workspace-import-scroller");
		expect(within(table).getByRole("columnheader", { name: "Item" })).toBeInTheDocument();
		expect(within(table).getByRole("columnheader", { name: "Decision" })).toBeInTheDocument();
		// The bulk control sits with the count, styled like the row controls.
		expect(screen.getByRole("button", { name: "Keep All" })).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Replace All" })).toBeInTheDocument();
		// Four items, three rows: the workflow and its file share one.
		expect(within(table).getAllByRole("row")).toHaveLength(4);
		expect(screen.getByText("2 items need review")).toBeInTheDocument();
		expect(screen.getByText("of 3 items")).toBeInTheDocument();
		expect(screen.getByText("Sink Alpha")).toBeInTheDocument();
		expect(screen.getByText("Replace moves this item to the selected scope")).toBeInTheDocument();
		// No match-key column and no file-path sub-line.
		expect(screen.queryByText("workflows/sink_alpha.py :: sink_alpha")).toBeNull();
		expect(screen.queryByText("workflows/sink_alpha.py")).toBeNull();
		// Type language matches Entity Management: full-word badges.
		expect(screen.getByText("Workflow")).toBeInTheDocument();
		expect(screen.getByText("App")).toBeInTheDocument();
		expect(screen.getByText("File")).toBeInTheDocument();
		expect(screen.getByText("Workflow")).toHaveClass("w-24");
		expect(screen.getByText("App")).toHaveClass("w-24");
		// No destination-ID callout line.
		expect(screen.queryByText(/Preserves destination ID/)).toBeNull();
		// No per-file control and no decided-counter noise.
		expect(screen.queryByText(/Same as/)).toBeNull();
		expect(screen.queryByText(/of \d+ decided/)).toBeNull();
	});

	it("decides a definition and its files together with one control", async () => {
		const onDecisionsChange = vi.fn();
		const { user } = renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={onDecisionsChange} />,
		);

		const rows = screen.getAllByRole("row");
		// Group row carries exactly one Keep/Replace pair for both members.
		const groupRow = rows[1];
		expect(within(groupRow).getByRole("button", { name: "Keep" })).toBeInTheDocument();
		await user.click(within(groupRow).getByRole("button", { name: "Replace" }));
		expect(onDecisionsChange).toHaveBeenCalledWith({
			"entity:workflow:alpha": "replace",
			"file:workflows/sink_alpha.py": "replace",
		});
	});

	it("counts a definition and its file as one review decision", () => {
		renderWithProviders(
			<WorkspaceImportReview
				preview={preview}
				decisions={{
					"entity:workflow:alpha": "replace",
					"file:workflows/sink_alpha.py": "replace",
				}}
				onDecisionsChange={vi.fn()}
			/>,
		);

		expect(screen.getByText("1 item needs review")).toBeInTheDocument();
	});

	it("resolves every conflict with Keep All", async () => {
		const onDecisionsChange = vi.fn();
		const { user } = renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={onDecisionsChange} />,
		);

		await user.click(screen.getByRole("button", { name: "Keep All" }));
		expect(onDecisionsChange).toHaveBeenCalledWith({
			"entity:workflow:alpha": "keep",
			"file:workflows/sink_alpha.py": "keep",
			"entity:app:customer": "keep",
		});
	});
});
