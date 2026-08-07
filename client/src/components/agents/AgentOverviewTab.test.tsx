/**
 * Tests for AgentOverviewTab.
 *
 * Mocks the per-agent stats and runs hooks at module scope so we can
 * exercise loading / data / empty branches deterministically.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";

function NavigationStateProbe() {
	const location = useLocation();
	return (
		<pre data-testid="navigation-state">
			{JSON.stringify(location.state)}
		</pre>
	);
}

const mockUseAgentStats = vi.fn();
vi.mock("@/services/agents", () => ({
	useAgentStats: (id: string | undefined) => mockUseAgentStats(id),
}));

const mockUseAgentRuns = vi.fn();
vi.mock("@/services/agentRuns", () => ({
	useAgentRuns: (params: unknown) => mockUseAgentRuns(params),
}));

const mockUseAgent = vi.fn();
vi.mock("@/hooks/useAgents", () => ({
	useAgent: (id: string | undefined) => mockUseAgent(id),
}));

const baseStats = {
	agent_id: "agent-1",
	runs_7d: 42,
	success_rate: 0.95,
	avg_duration_ms: 1500,
	total_cost_7d: "1.23",
	last_run_at: "2026-04-21T10:00:00Z",
	runs_by_day: [1, 2, 3, 4, 5, 6, 7],
	needs_review: 2,
	unreviewed: 0,
};

function makeRun(overrides: Record<string, unknown> = {}) {
	return {
		id: "run-1",
		agent_id: "agent-1",
		agent_name: "Triage",
		trigger_type: "test",
		status: "completed",
		iterations_used: 1,
		tokens_used: 1000,
		asked: "Help me",
		did: "Routed",
		input: {},
		output: {},
		verdict: null,
		verdict_note: null,
		duration_ms: 800,
		created_at: "2026-04-20T00:00:00Z",
		started_at: "2026-04-20T00:00:00Z",
		metadata: {},
		...overrides,
	};
}

beforeEach(() => {
	mockUseAgentStats.mockReturnValue({ data: baseStats, isLoading: false });
	mockUseAgentRuns.mockReturnValue({
		data: { items: [makeRun()], total: 1, next_cursor: null },
		isLoading: false,
	});
	mockUseAgent.mockReturnValue({
		data: {
			id: "agent-1",
			name: "Triage",
			description: "Test",
			channels: ["chat"],
			access_level: "authenticated",
			created_by: "admin",
			llm_model: null,
			max_iterations: 15,
			max_token_budget: 50000,
			is_active: true,
		},
		isLoading: false,
	});
});

async function renderTab(agentId = "agent-1") {
	const { AgentOverviewTab } = await import("./AgentOverviewTab");
	return renderWithProviders(<AgentOverviewTab agentId={agentId} />);
}

describe("AgentOverviewTab", () => {
	it("renders the per-agent stats", async () => {
		await renderTab();
		expect(screen.getByText("42")).toBeInTheDocument(); // runs
		expect(screen.getByText("95%")).toBeInTheDocument(); // success rate
	});

	it("renders the recent runs list", async () => {
		await renderTab();
		expect(screen.getByText(/help me/i)).toBeInTheDocument();
		expect(
			screen.getByRole("img", { name: "Status: Completed" }),
		).toBeInTheDocument();
	});

	it("keeps the overview as the origin when a recent run is opened", async () => {
		const { AgentOverviewTab } = await import("./AgentOverviewTab");
		const { user } = renderWithProviders(
			<Routes>
				<Route
					path="/agents/:agentId"
					element={<AgentOverviewTab agentId="agent-1" />}
				/>
				<Route
					path="/agents/:agentId/runs/:runId"
					element={<NavigationStateProbe />}
				/>
			</Routes>,
			{ initialEntries: ["/agents/agent-1?tab=overview"] },
		);

		await user.click(screen.getByRole("link", { name: /help me/i }));
		expect(screen.getByTestId("navigation-state")).toHaveTextContent(
			JSON.stringify({
				agentRunOrigin: {
					href: "/agents/agent-1?tab=overview",
					label: "Back to Triage overview",
				},
			}),
		);
	});

	it("links View all runs directly to the Runs tab", async () => {
		await renderTab();
		expect(
			screen.getByRole("link", { name: /view all runs/i }),
		).toHaveAttribute("href", "/agents/agent-1?tab=runs");
	});

	it("hides the 'Needs attention' card when no flagged runs", async () => {
		mockUseAgentStats.mockReturnValue({
			data: { ...baseStats, needs_review: 0, unreviewed: 0 },
			isLoading: false,
		});
		mockUseAgentRuns.mockReturnValue({
			data: { items: [makeRun()], total: 1, next_cursor: null },
			isLoading: false,
		});
		await renderTab();
		// No red "Needs attention" card should render when everything is green
		expect(screen.queryByText(/needs attention/i)).not.toBeInTheDocument();
	});

	it("renders 'Needs attention' card with count from stats.needs_review", async () => {
		mockUseAgentStats.mockReturnValue({
			data: { ...baseStats, needs_review: 2, unreviewed: 0 },
			isLoading: false,
		});
		await renderTab();
		expect(screen.getByText(/needs attention/i)).toBeInTheDocument();
		expect(screen.getByText(/open review flipbook/i)).toBeInTheDocument();
	});

	it("shows the 'to review' card driven by stats.unreviewed, not the runs page", async () => {
		// Stats report 47 unreviewed across the window; runs page only has 3
		// completed items with no verdict. The card must reflect stats (47),
		// NOT the runs-page count (3). This guards the bug where the count
		// was frozen at the 10-run page size.
		mockUseAgentStats.mockReturnValue({
			data: { ...baseStats, needs_review: 0, unreviewed: 47 },
			isLoading: false,
		});
		mockUseAgentRuns.mockReturnValue({
			data: {
				items: [
					makeRun(),
					makeRun({ id: "run-2" }),
					makeRun({ id: "run-3" }),
				],
				total: 3,
				next_cursor: null,
			},
			isLoading: false,
		});
		await renderTab();
		expect(screen.getByText(/47 to review/i)).toBeInTheDocument();
	});

	it("hides the to-review card when stats.unreviewed and needs_review are both zero", async () => {
		mockUseAgentStats.mockReturnValue({
			data: { ...baseStats, needs_review: 0, unreviewed: 0 },
			isLoading: false,
		});
		await renderTab();
		expect(screen.queryByText(/to review/i)).not.toBeInTheDocument();
		expect(screen.queryByText(/needs attention/i)).not.toBeInTheDocument();
	});

	it("shows skeletons while loading stats", async () => {
		mockUseAgentStats.mockReturnValue({ data: undefined, isLoading: true });
		const { container } = await renderTab();
		expect(
			container.querySelectorAll(".animate-pulse").length,
		).toBeGreaterThan(0);
	});

	it("shows the no-runs message when the recent runs list is empty", async () => {
		mockUseAgentRuns.mockReturnValue({
			data: { items: [], total: 0, next_cursor: null },
			isLoading: false,
		});
		await renderTab();
		expect(
			screen.getByText(/no runs yet for this agent/i),
		).toBeInTheDocument();
	});
});
