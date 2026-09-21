import { describe, it, expect, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";
import { within } from "@testing-library/react";
import type { components } from "@/lib/v1";

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true }),
}));

vi.mock("@/services/agentRuns", () => ({
	useRegenerateSummary: () => ({ mutate: vi.fn(), isPending: false }),
	useAgentRun: () => ({
		data: undefined,
		isLoading: false,
		isError: false,
	}),
}));

import { RunReviewSheet } from "./RunReviewSheet";

type AgentRunDetail = components["schemas"]["AgentRunDetailResponse"];

function NavigationStateProbe() {
	const location = useLocation();
	return (
		<pre data-testid="navigation-state">
			{JSON.stringify(location.state)}
		</pre>
	);
}

const baseRun: AgentRunDetail = {
	id: "00000000-0000-0000-0000-000000000001",
	agent_id: "00000000-0000-0000-0000-000000000002",
	agent_name: "Tier-1 Triage",
	trigger_type: "test",
	summary_status: "completed",
	status: "completed",
	iterations_used: 1,
	tokens_used: 100,
	asked: "How do I reset my password?",
	did: "Routed to Support",
	input: { message: "help" },
	output: { text: "ok" },
	verdict: null,
	verdict_note: null,
	created_at: "2026-04-21T10:00:00Z",
	metadata: {},
	steps: [],
};

const activityRun: AgentRunDetail = {
	...baseRun,
	did: "Looked up [get_ticket] before routing.",
	answered: "The ticket was routed.",
	steps: [
		{
			id: "step-1",
			run_id: baseRun.id,
			step_number: 1,
			type: "tool_call",
			content: {
				tool_name: "get_ticket",
				arguments: { ticket_id: 428950 },
			},
			created_at: baseRun.created_at,
		},
		{
			id: "step-2",
			run_id: baseRun.id,
			step_number: 2,
			type: "tool_result",
			content: {
				tool_name: "get_ticket",
				result: { ticket_id: 428950, status: "open" },
			},
			created_at: baseRun.created_at,
		},
	],
};

describe("RunReviewSheet", () => {
	it("shows a loading sheet while run details are unavailable", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={null}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getByRole("dialog", { name: "Run review" }),
		).toBeVisible();
		expect(screen.getByRole("status")).toHaveTextContent(
			"Loading run details",
		);
	});

	it("renders sheet content with run title when open", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		const titles = screen.getAllByText(/routed to support/i);
		expect(titles.length).toBeGreaterThan(0);
		expect(
			screen.queryByRole("tab", { name: /discussion/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByText(/improvement conversation/i),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /close run review/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: /open full run/i }),
		).toHaveClass("h-11");
	});

	it("renders markdown in the sheet title without showing markers", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={{ ...baseRun, asked: "Reset **password** now" }}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);

		const title = screen.getByRole("heading", {
			name: /reset password now/i,
		});
		expect(within(title).getByText("password").tagName.toLowerCase()).toBe(
			"strong",
		);
		expect(screen.queryByText(/\*\*/)).not.toBeInTheDocument();
	});

	it("renders the Review tab content by default", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		// Review tab body shows the asked text. Title also renders `asked`
		// (sheet headers use it as the TL;DR), so multiple matches are fine.
		expect(
			screen.getAllByText(/how do i reset my password/i).length,
		).toBeGreaterThan(0);
	});

	it("shows the human activity view and links summary references to it", async () => {
		const { user } = renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={activityRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);

		expect(
			screen.getByRole("region", { name: "Activity" }),
		).toBeInTheDocument();
		const activityRegion = screen.getByRole("region", {
			name: "Activity",
		});
		expect(activityRegion).toHaveTextContent("Looked up ticket");
		expect(
			screen.getByText("Ticket: 428950 · Status: Open"),
		).toBeInTheDocument();
		expect(
			screen.queryByText("Raw executor trace"),
		).not.toBeInTheDocument();

		const reference = screen.getByRole("link", {
			name: "Show Looked up ticket in Activity",
		});
		const activity = document.querySelector('[data-activity-id="step-1"]');
		expect(activity).toHaveAttribute("data-highlighted", "false");

		await user.hover(reference);
		expect(activity).toHaveAttribute("data-highlighted", "true");
	});

	it("shows a Finding action for a negatively reviewed run", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict="down"
				note="Skipped approval"
				onVerdict={() => {}}
				onNote={() => {}}
				reviewActions={<button type="button">Create Finding</button>}
			/>,
		);
		expect(
			screen.getByRole("button", { name: "Create Finding" }),
		).toBeInTheDocument();
		expect(screen.queryByText(/discussion/i)).not.toBeInTheDocument();
	});

	it("keeps negative reviews on the review surface", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict="down"
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		expect(
			screen.getAllByText(/how do i reset my password/i).length,
		).toBeGreaterThan(0);
		expect(
			screen.queryByText(/flag this run and tell me what went wrong/i),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("tab", { name: /discussion/i }),
		).not.toBeInTheDocument();
	});

	it("calls onOpenChange(false) when the close button is clicked", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={onOpenChange}
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		await user.click(
			screen.getByRole("button", { name: /close run review/i }),
		);
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});

	it("exposes an 'Open full run' link in the header pointing at the run page", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		const link = screen.getByRole("link", { name: /open full run/i });
		expect(link).toHaveAttribute(
			"href",
			`/agents/${baseRun.agent_id}/runs/${baseRun.id}`,
		);
		expect(link).toHaveClass("h-11");
	});

	it("keeps the sheet container full-height and motion-reduce aware", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
			/>,
		);
		const sheetContent = document.querySelector(
			'[data-slot="sheet-content"]',
		);
		expect(sheetContent).toHaveClass("h-full");
		expect(sheetContent).toHaveClass("motion-reduce:transition-none");
	});

	it("returns full-run navigation to the runs view that opened the sheet", async () => {
		const { user } = renderWithProviders(
			<Routes>
				<Route
					path="/agents/:agentId"
					element={
						<RunReviewSheet
							open={true}
							onOpenChange={() => {}}
							run={baseRun}
							verdict={null}
							note=""
							onVerdict={() => {}}
							onNote={() => {}}
						/>
					}
				/>
				<Route
					path="/agents/:agentId/runs/:runId"
					element={<NavigationStateProbe />}
				/>
			</Routes>,
			{
				initialEntries: [
					`/agents/${baseRun.agent_id}?tab=runs&summary=failed`,
				],
			},
		);

		await user.click(screen.getByRole("link", { name: /open full run/i }));
		expect(screen.getByTestId("navigation-state")).toHaveTextContent(
			JSON.stringify({
				agentRunOrigin: {
					href: `/agents/${baseRun.agent_id}?tab=runs&summary=failed`,
					label: "Back to Tier-1 Triage runs",
				},
			}),
		);
	});
});
