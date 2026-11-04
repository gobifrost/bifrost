import { describe, it, expect } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";

import { TuneHeader } from "./TuneHeader";

function renderHeader(
	props: Partial<React.ComponentProps<typeof TuneHeader>> = {},
) {
	return render(
		<MemoryRouter>
			<TuneHeader
				agentId="agent-1"
				agentName="Test Parent Agent"
				flaggedCount={2}
				stats={{
					runs_7d: 47,
					success_rate: 0.92,
					avg_duration_ms: 1200,
					total_cost_7d: "0.42",
					last_run_at: "2026-04-22T00:00:00Z",
					runs_by_day: [],
					needs_review: 2,
					unreviewed: 2,
					agent_id: "agent-1",
				}}
				statsLoading={false}
				{...props}
			/>
		</MemoryRouter>,
	);
}

describe("TuneHeader", () => {
	it("renders the agent breadcrumb and page title", () => {
		renderHeader();
		expect(
			screen.getByRole("link", { name: /test parent agent/i }),
		).toHaveAttribute("href", "/agents/agent-1");
		expect(
			screen.getByRole("heading", { name: /tune agent/i }),
		).toBeInTheDocument();
	});

	it("keeps statistics in a compact inline strip", () => {
		renderHeader();
		expect(screen.getByLabelText("Agent Statistics")).toHaveTextContent(
			"2 Flagged Runs",
		);
		expect(screen.getByLabelText("Agent Statistics")).toHaveTextContent(
			"47",
		);
		expect(screen.getByLabelText("Agent Statistics")).toHaveTextContent(
			"92%",
		);
	});
	it("shows an inline loading state for statistics", () => {
		renderHeader({ stats: null, statsLoading: true });
		expect(screen.getByTestId("stat-skeleton")).toBeInTheDocument();
	});

	it("renders the Review flagged runs link next to the breadcrumb", () => {
		renderHeader();
		expect(
			screen.getByRole("link", { name: /review flagged runs/i }),
		).toHaveAttribute("href", "/agents/agent-1/review");
	});

	it("renders the action slot when provided", () => {
		renderHeader({
			action: <button data-testid="header-action">Run dry-run</button>,
		});
		expect(screen.getByTestId("header-action")).toBeInTheDocument();
	});

	it("does not render the action area when no action is provided", () => {
		renderHeader();
		expect(screen.queryByTestId("header-action")).toBeNull();
	});
});
