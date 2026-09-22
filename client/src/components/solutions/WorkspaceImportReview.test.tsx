import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { WorkspaceImportReview } from "./WorkspaceImportReview";

const preview = {
	preview_token: "preview-1",
	package_name: "Customer operations",
	package_sha256: "a".repeat(64),
	source_kind: "zip",
	warnings: ["Imported entities are unattached global workspace content."],
	items: [
		{
			id: "entity:workflow:alpha",
			kind: "workflow",
			name: "Sink Alpha",
			classification: "conflict",
			match_key: "workflows/sink_alpha.py :: sink_alpha",
			target_id: "destination-alpha-id",
			group_key: "file:workflows/sink_alpha.py",
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
	it("states the compatibility warning simply and collapses package notices", async () => {
		const { user } = renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={vi.fn()} />,
		);

		expect(
			screen.getByText(/Solutions are designed to work together\./),
		).toBeInTheDocument();
		// Declaration warnings hide behind one labeled expander, not a wall.
		expect(screen.getByText(/Imported entities are unattached/)).not.toBeVisible();
		await user.click(screen.getByText(/Package notices \(1\)/));
		expect(
			screen.getByText(/Imported entities are unattached/),
		).toBeVisible();
	});

	it("renders one row per definition with its files, in a standard table", async () => {
		renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={vi.fn()} />,
		);

		const table = screen.getByTestId("workspace-import-scroller");
		expect(within(table).getByRole("columnheader", { name: "Item" })).toBeInTheDocument();
		expect(within(table).getByRole("columnheader", { name: "Matched by" })).toBeInTheDocument();
		expect(within(table).getByRole("columnheader", { name: "Decision" })).toBeInTheDocument();
		// Four items, three rows: the workflow and its file share one.
		expect(within(table).getAllByRole("row")).toHaveLength(4);
		expect(screen.getByText("Sink Alpha")).toBeInTheDocument();
		expect(screen.getByText("workflows/sink_alpha.py :: sink_alpha")).toBeInTheDocument();
		// Destination identity stays on the row, not in a side panel.
		expect(screen.getAllByText(/Preserves destination ID/)).toHaveLength(2);
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

	it("resolves every conflict with Keep all", async () => {
		const onDecisionsChange = vi.fn();
		const { user } = renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={onDecisionsChange} />,
		);

		await user.click(screen.getByRole("button", { name: "Keep all" }));
		expect(onDecisionsChange).toHaveBeenCalledWith({
			"entity:workflow:alpha": "keep",
			"file:workflows/sink_alpha.py": "keep",
			"entity:app:customer": "keep",
		});
	});
});
