import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { WorkbenchRow } from "./WorkbenchRow";

describe("WorkbenchRow", () => {
	it("communicates selection and makes the full row the primary target", async () => {
		const onSelect = vi.fn();
		const { user } = renderWithProviders(
			<WorkbenchRow
				title="Should confirm routing"
				meta="Failed simulation · 2 minutes ago"
				selected
				onSelect={onSelect}
			/>,
		);

		const row = screen.getByRole("button", {
			name: /should confirm routing/i,
		});
		expect(row).toHaveAttribute("aria-selected", "true");
		expect(row).toHaveClass("tree-row-selected");

		await user.click(row);
		expect(onSelect).toHaveBeenCalledOnce();
	});
});
