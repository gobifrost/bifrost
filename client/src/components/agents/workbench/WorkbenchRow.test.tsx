import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { WorkbenchRow } from "./WorkbenchRow";

describe("WorkbenchRow", () => {
	it("uses native list semantics with independently focusable controls", async () => {
		const onSelect = vi.fn();
		const onNestedAction = vi.fn();
		const { user } = renderWithProviders(
			<div role="list" aria-label="Tests collection">
				<WorkbenchRow
					title="Should confirm routing"
					meta="Failed simulation · 2 minutes ago"
					leading={<span>!</span>}
					leadingLabel="Failed Test"
					selected
					onSelect={onSelect}
					selectionControl={
						<input aria-label="Select test" type="checkbox" />
					}
					actions={
						<button type="button" onClick={onNestedAction}>
							Open actions
						</button>
					}
				/>
			</div>,
		);

		const list = screen.getByRole("list", { name: "Tests collection" });
		const item = screen.getByRole("listitem");
		expect(screen.getByRole("img", { name: "Failed Test" })).toBeVisible();
		const primaryButton = screen.getByRole("button", {
			name: /should confirm routing/i,
		});
		expect(item.parentElement).toBe(list);
		expect(item).toHaveClass("tree-row-selected");
		expect(primaryButton).toHaveAttribute("aria-current", "true");
		expect(primaryButton.querySelectorAll("div")).toHaveLength(0);

		await user.tab();
		expect(
			screen.getByRole("checkbox", { name: "Select test" }),
		).toHaveFocus();
		await user.tab();
		expect(primaryButton).toHaveFocus();
		await user.keyboard("{Enter}");
		await user.keyboard(" ");
		expect(onSelect).toHaveBeenCalledTimes(2);
		await user.tab();
		expect(
			screen.getByRole("button", { name: "Open actions" }),
		).toHaveFocus();

		await user.click(screen.getByRole("checkbox", { name: "Select test" }));
		await user.click(screen.getByRole("button", { name: "Open actions" }));
		expect(onNestedAction).toHaveBeenCalledOnce();
		expect(onSelect).toHaveBeenCalledTimes(2);
	});
});
