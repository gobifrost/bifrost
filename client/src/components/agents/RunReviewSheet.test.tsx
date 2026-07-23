import { describe, it, expect, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";
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
type FlagConversationResponse =
	components["schemas"]["FlagConversationResponse"];

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

const baseConversation: FlagConversationResponse = {
	id: "00000000-0000-0000-0000-0000000000c1",
	run_id: baseRun.id,
	messages: [],
	created_at: baseRun.created_at,
	last_updated_at: baseRun.created_at,
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
	it("renders nothing when run is null", () => {
		const { container } = renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={null}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
				conversation={null}
				onSendChat={() => {}}
			/>,
		);
		expect(container.firstChild).toBeNull();
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
				conversation={baseConversation}
				onSendChat={() => {}}
			/>,
		);
		const titles = screen.getAllByText(/routed to support/i);
		expect(titles.length).toBeGreaterThan(0);
		expect(
			screen.getByRole("tab", { name: /^review$/i }),
		).toBeInTheDocument();
		expect(screen.getByRole("tab", { name: /tune/i })).toBeInTheDocument();
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
				conversation={baseConversation}
				onSendChat={() => {}}
			/>,
		);
		// Review tab body shows the asked text. Title also renders `asked`
		// (sheet headers use it as the TL;DR), so multiple matches are fine.
		expect(
			screen.getAllByText(/how do i reset my password/i).length,
		).toBeGreaterThan(0);
	});

	it("shows the human activity view and links summary references to it", async () => {
		const scrollIntoView = vi.fn();
		Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
			configurable: true,
			value: scrollIntoView,
		});
		const { user } = renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={activityRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
				conversation={baseConversation}
				onSendChat={() => {}}
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
		await user.hover(
			screen.getByRole("heading", { name: "Activity" }),
		);
		expect(activity).toHaveAttribute("data-highlighted", "false");

		await user.click(reference);
		expect(scrollIntoView).toHaveBeenCalledWith({
			behavior: "smooth",
			block: "center",
		});
		await user.hover(
			screen.getByRole("heading", { name: "Activity" }),
		);
		expect(activity).toHaveFocus();
		expect(activity).toHaveAttribute("data-highlighted", "false");
	});

	it("switches to Tune tab on click", async () => {
		const { user } = renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict={null}
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
				conversation={baseConversation}
				onSendChat={() => {}}
			/>,
		);
		await user.click(screen.getByRole("tab", { name: /tune/i }));
		// FlagConversation empty state is visible after switching
		expect(
			screen.getByText(/flag this run and tell me what went wrong/i),
		).toBeInTheDocument();
	});

	it("starts on the tune tab when defaultTab='tune'", () => {
		renderWithProviders(
			<RunReviewSheet
				open={true}
				onOpenChange={() => {}}
				run={baseRun}
				verdict="down"
				note=""
				onVerdict={() => {}}
				onNote={() => {}}
				conversation={baseConversation}
				onSendChat={() => {}}
				defaultTab="tune"
			/>,
		);
		expect(
			screen.getByText(/flag this run and tell me what went wrong/i),
		).toBeInTheDocument();
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
				conversation={baseConversation}
				onSendChat={() => {}}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /close/i }));
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
				conversation={baseConversation}
				onSendChat={() => {}}
			/>,
		);
		const link = screen.getByRole("link", { name: /open full run/i });
		expect(link).toHaveAttribute(
			"href",
			`/agents/${baseRun.agent_id}/runs/${baseRun.id}`,
		);
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
							conversation={baseConversation}
							onSendChat={() => {}}
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

		await user.click(
			screen.getByRole("link", { name: /open full run/i }),
		);
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
