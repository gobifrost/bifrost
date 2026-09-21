import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { WorkbenchCollectionToolbar } from "./WorkbenchCollectionToolbar";

describe("WorkbenchCollectionToolbar", () => {
	it("keeps search, selection context, and collection actions together", () => {
		const onSearchChange = vi.fn();
		renderWithProviders(
			<WorkbenchCollectionToolbar
				collectionLabel="Tests"
				search="routing"
				onSearchChange={onSearchChange}
				selectionCount={2}
				secondaryAction={<button type="button">Add Test</button>}
				primaryAction={{ type: "button", children: "Run Simulation" }}
			>
				<label>
					Profile
					<select defaultValue="default">
						<option value="default">Default assignment</option>
					</select>
				</label>
			</WorkbenchCollectionToolbar>,
		);

		expect(screen.getByLabelText("Search Tests")).toHaveValue("routing");
		expect(screen.getByText("2 selected")).toBeVisible();
		expect(screen.getByRole("button", { name: "Add Test" })).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Run Simulation" }),
		).toBeVisible();
		expect(screen.getByLabelText("Profile")).toBeVisible();
	});
});
