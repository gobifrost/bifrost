import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import { AgentWorkbenchFrame } from "./AgentWorkbenchFrame";

describe("AgentWorkbenchFrame", () => {
	const collections = [
		{ value: "tests", label: "Tests" },
		{ value: "findings", label: "Findings" },
	];

	it("keeps collection navigation and the selected inspector in one workspace", () => {
		renderWithProviders(
			<AgentWorkbenchFrame
				title="Workbench"
				collections={collections}
				collection="tests"
				onCollectionChange={() => undefined}
				toolbar={<div>Collection toolbar</div>}
				inspector={<div>Test inspector</div>}
				onCloseInspector={() => undefined}
			>
				<div>Test rows</div>
			</AgentWorkbenchFrame>,
		);

		expect(screen.getByLabelText("Workbench collections")).toBeVisible();
		expect(screen.getByRole("button", { name: "Tests" })).toHaveAttribute(
			"aria-current",
			"page",
		);
		expect(screen.getByText("Collection toolbar")).toBeVisible();
		expect(screen.getByText("Test rows")).toBeVisible();
		expect(screen.getByText("Test inspector")).toBeVisible();
		expect(screen.getByRole("button", { name: "Close inspector" })).toBeVisible();
	});

	it("uses a collection selector on narrow screens", () => {
		renderWithProviders(
			<AgentWorkbenchFrame
				title="Agent Workbench"
				collections={collections}
				collection="findings"
				onCollectionChange={() => undefined}
				toolbar={<div>Collection toolbar</div>}
			>
				<div>Finding rows</div>
			</AgentWorkbenchFrame>,
		);

		expect(
			screen.getByRole("combobox", { name: "Workbench collection" }),
		).toHaveTextContent("Findings");
	});
});
