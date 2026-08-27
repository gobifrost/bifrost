import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders, screen } from "@/test-utils";

const mockUseInfiniteAgentRuns = vi.hoisted(() => vi.fn());
const mockUseRerunAgentRun = vi.hoisted(() => vi.fn());

vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: () => mockUseInfiniteAgentRuns(),
	useAgentRunListStream: () => undefined,
	useRerunAgentRun: () => mockUseRerunAgentRun(),
}));

import { AgentRunsPanel } from "./AgentRunsPanel";

function NavigationStateProbe() {
	const location = useLocation();
	return (
		<pre data-testid="navigation-state">
			{JSON.stringify(location.state)}
		</pre>
	);
}

const run = {
	id: "run-1",
	agent_id: "agent-1",
	agent_name: "Service Desk Triage",
	trigger_type: "manual",
	status: "completed",
	iterations_used: 2,
	tokens_used: 1200,
	asked: "Triage ticket 428950",
	did: "Triaged the ticket.",
	input: {},
	output: {},
	verdict: null,
	created_at: "2026-07-23T12:00:00Z",
	started_at: "2026-07-23T12:00:00Z",
};

beforeEach(() => {
	mockUseInfiniteAgentRuns.mockReturnValue({
		data: { pages: [{ items: [run], total: 1 }] },
		isLoading: false,
		hasNextPage: false,
		isFetchingNextPage: false,
		fetchNextPage: vi.fn(),
	});
	mockUseRerunAgentRun.mockReturnValue({
		mutate: vi.fn(),
		isPending: false,
	});
});

describe("AgentRunsPanel", () => {
	it("constrains the table and progressively collapses secondary columns", () => {
		renderWithProviders(
			<Routes>
				<Route path="/history" element={<AgentRunsPanel />} />
			</Routes>,
			{ initialEntries: ["/history?type=agents"] },
		);

		const table = screen.getByRole("table");
		expect(table.parentElement?.parentElement).toHaveClass(
			"min-h-0",
			"min-w-0",
		);
		expect(screen.getByRole("columnheader", { name: "Agent" })).toHaveClass(
			"w-full",
			"sm:w-40",
		);
		expect(screen.getByRole("columnheader", { name: "Asked" })).toHaveClass(
			"hidden",
			"sm:table-cell",
		);
		expect(screen.getByRole("columnheader", { name: "Duration" })).toHaveClass(
			"hidden",
			"lg:table-cell",
		);
		expect(screen.getByRole("columnheader", { name: "Verdict" })).toHaveClass(
			"hidden",
			"xl:table-cell",
		);
		expect(screen.getByRole("columnheader", { name: "Started" })).toHaveClass(
			"hidden",
			"xl:table-cell",
		);
	});

	it("keeps fleet run history as the origin when opening a run", async () => {
		const { user } = renderWithProviders(
			<Routes>
				<Route path="/history" element={<AgentRunsPanel />} />
				<Route
					path="/agents/:agentId/runs/:runId"
					element={<NavigationStateProbe />}
				/>
			</Routes>,
			{ initialEntries: ["/history?type=agents"] },
		);

		await user.click(
			screen.getByRole("row", { name: /Service Desk Triage.*Completed/i }),
		);
		expect(screen.getByTestId("navigation-state")).toHaveTextContent(
			JSON.stringify({
				agentRunOrigin: {
					href: "/history?type=agents",
					label: "Back to run history",
				},
			}),
		);
	});
});
