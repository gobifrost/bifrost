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
	it("states the compatibility warning simply and keeps declaration warnings visible", async () => {
		renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={vi.fn()} />,
		);

		expect(
			screen.getByText(/Solutions are designed to work together\./),
		).toBeInTheDocument();
		expect(
			screen.getByText(/Imported entities are unattached/),
		).toBeInTheDocument();
	});

	it("renders one standard table with a sticky header and full names", async () => {
		renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={vi.fn()} />,
		);

		const table = screen.getByTestId("workspace-import-scroller");
		expect(within(table).getByRole("columnheader", { name: "Item" })).toBeInTheDocument();
		expect(within(table).getByRole("columnheader", { name: "Matched by" })).toBeInTheDocument();
		expect(within(table).getByRole("columnheader", { name: "Decision" })).toBeInTheDocument();
		// Full names wrap instead of truncating into unreadable fragments.
		expect(screen.getByText("Sink Alpha")).toBeInTheDocument();
		expect(screen.getByText("workflows/sink_alpha.py :: sink_alpha")).toBeInTheDocument();
		// Destination identity stays on the row, not in a side panel.
		expect(screen.getAllByText(/Preserves destination ID/)).toHaveLength(2);
	});

	it("decides a definition and its files together with one control", async () => {
		const onDecisionsChange = vi.fn();
		const { user } = renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={onDecisionsChange} />,
		);

		// The file row carries no control of its own — it follows the definition.
		expect(screen.getByText("Same as Sink Alpha")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Replace all" }));
		expect(onDecisionsChange).toHaveBeenCalledWith({
			"entity:workflow:alpha": "replace",
			"file:workflows/sink_alpha.py": "replace",
			"entity:app:customer": "replace",
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
