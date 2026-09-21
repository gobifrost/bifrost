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

	it("associates each mounted search control with a distinct label", () => {
		renderWithProviders(
			<>
				<WorkbenchCollectionToolbar
					collectionLabel="Tests"
					search=""
					onSearchChange={vi.fn()}
				/>
				<WorkbenchCollectionToolbar
					collectionLabel="Tests"
					search=""
					onSearchChange={vi.fn()}
				/>
			</>,
		);

		const inputs = screen.getAllByRole("textbox", { name: "Search Tests" });
		const labels = Array.from(document.querySelectorAll("label")).filter(
			(label) => label.textContent === "Search Tests",
		);

		expect(inputs).toHaveLength(2);
		expect(inputs[0]?.id).not.toBe(inputs[1]?.id);
		expect(labels[0]).toHaveAttribute("for", inputs[0]?.id);
		expect(labels[1]).toHaveAttribute("for", inputs[1]?.id);
	});
});
