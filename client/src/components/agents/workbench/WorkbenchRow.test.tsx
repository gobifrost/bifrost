import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, within } from "@/test-utils";

import { WorkbenchRow } from "./WorkbenchRow";

describe("WorkbenchRow", () => {
	it("uses grid row semantics while preserving nested control isolation", async () => {
		const onSelect = vi.fn();
		const onNestedAction = vi.fn();
		const { user } = renderWithProviders(
			<div role="grid" aria-label="Tests collection">
				<WorkbenchRow
					title="Should confirm routing"
					meta="Failed simulation · 2 minutes ago"
					selected
					onSelect={onSelect}
					selectionControl={<input aria-label="Select test" type="checkbox" />}
					actions={
						<button type="button" onClick={onNestedAction}>
							Open actions
						</button>
					}
				/>
			</div>,
		);

		const grid = screen.getByRole("grid", { name: "Tests collection" });
		const row = screen.getByRole("row", {
			name: /should confirm routing/i,
		});
		expect(row.parentElement).toBe(grid);
		expect(within(row).getAllByRole("gridcell")).toHaveLength(3);
		expect(row).toHaveAttribute("aria-selected", "true");
		expect(row).toHaveClass("tree-row-selected");

		await user.click(row);
		await user.keyboard("{Enter}");
		await user.keyboard(" ");
		expect(onSelect).toHaveBeenCalledTimes(3);

		await user.click(screen.getByRole("checkbox", { name: "Select test" }));
		await user.click(screen.getByRole("button", { name: "Open actions" }));
		expect(onNestedAction).toHaveBeenCalledOnce();
		expect(onSelect).toHaveBeenCalledTimes(3);
	});
});
