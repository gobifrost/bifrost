import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { WorkspaceImportReview } from "./WorkspaceImportReview";

const preview = {
	preview_token: "preview-1",
	package_name: "Customer operations",
	package_sha256: "a".repeat(64),
	warnings: [],
	items: [
		{ id: "entity:app:customer", kind: "app", name: "Customer dashboard", classification: "conflict", match_key: "slug", diff: [] },
		{ id: "file:modules/customer.py", kind: "file", name: "modules/customer.py", classification: "create", diff: [] },
	],
} as never;

describe("WorkspaceImportReview", () => {
	it("uses one scroll-owned review body and resolves every conflict with Replace all", async () => {
		const onDecisionsChange = vi.fn();
		const { user } = renderWithProviders(
			<WorkspaceImportReview preview={preview} decisions={{}} onDecisionsChange={onDecisionsChange} />,
		);

		expect(screen.getByTestId("workspace-import-scroller")).toHaveClass("overflow-y-auto");
		await user.click(screen.getByRole("button", { name: "Replace all" }));
		expect(onDecisionsChange).toHaveBeenCalledWith({ "entity:app:customer": "replace" });
	});
});
