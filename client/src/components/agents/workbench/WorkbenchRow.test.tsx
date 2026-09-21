import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, within } from "@/test-utils";

import { WorkbenchRow } from "./WorkbenchRow";

describe("WorkbenchRow", () => {
	it("uses grid row semantics, roving focus, and nested control isolation", async () => {
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
				<WorkbenchRow
					title="Should handle errors"
					onSelect={onSelect}
					tabIndex={-1}
				/>
				<WorkbenchRow
					title="Should summarize safely"
					onSelect={onSelect}
					tabIndex={-1}
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
		expect(row).toHaveAttribute("tabindex", "0");
		const errorRow = screen.getByRole("row", { name: /should handle errors/i });
		const safeRow = screen.getByRole("row", { name: /should summarize safely/i });
		expect(errorRow).toHaveAttribute("tabindex", "-1");
		expect(safeRow).toHaveAttribute("tabindex", "-1");

		await user.click(row);
		await user.keyboard("{Enter}");
		await user.keyboard(" ");
		expect(onSelect).toHaveBeenCalledTimes(3);

		row.focus();
		await user.keyboard("{ArrowDown}");
		expect(errorRow).toHaveFocus();
		expect(errorRow).toHaveAttribute("tabindex", "0");
		expect(row).toHaveAttribute("tabindex", "-1");
		await user.keyboard("{End}");
		expect(safeRow).toHaveFocus();
		await user.keyboard("{ArrowUp}");
		expect(errorRow).toHaveFocus();
		await user.keyboard("{Home}");
		expect(row).toHaveFocus();
		await user.keyboard("{ArrowUp}");
		expect(row).toHaveFocus();

		await user.click(screen.getByRole("checkbox", { name: "Select test" }));
		await user.click(screen.getByRole("button", { name: "Open actions" }));
		expect(onNestedAction).toHaveBeenCalledOnce();
		expect(onSelect).toHaveBeenCalledTimes(3);
	});
});
