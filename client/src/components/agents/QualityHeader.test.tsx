import { describe, it, expect } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";

import { QualityHeader } from "./QualityHeader";

function renderHeader(
	props: Partial<React.ComponentProps<typeof QualityHeader>> = {},
) {
	return render(
		<MemoryRouter>
			<QualityHeader
				agentId="agent-1"
				agentName="Test Parent Agent"
				{...props}
			/>
		</MemoryRouter>,
	);
}

describe("QualityHeader", () => {
	it("renders the agent backlink and the workbench title", () => {
		renderHeader();
		expect(
			screen.getByRole("link", { name: /test parent agent/i }),
		).toHaveAttribute("href", "/agents/agent-1");
		expect(
			screen.getByRole("heading", { name: "Workbench" }),
		).toBeInTheDocument();
		expect(screen.queryByText(/quality workbench/i)).not.toBeInTheDocument();
		expect(
			screen.getByText(
				/Review runs, turn findings into tests, compare changes, and inspect results\./,
			),
		).toBeVisible();
	});

	it("falls back to the agents list without an agent", () => {
		renderHeader({ agentId: undefined, agentName: undefined });
		expect(
			screen.getByRole("link", { name: /back to agent/i }),
		).toHaveAttribute("href", "/agents");
	});

	it("shows no production statistics in the workbench header", () => {
		renderHeader();
		expect(screen.queryByText("Runs (7d)")).not.toBeInTheDocument();
		expect(screen.queryByText("Success rate")).not.toBeInTheDocument();
		expect(screen.queryByText(/tuning/i)).not.toBeInTheDocument();
	});
});
