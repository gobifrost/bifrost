import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import { fireEvent, renderWithProviders, screen } from "@/test-utils";

const mockIsDesktop = vi.hoisted(() => vi.fn(() => true));
vi.mock("@/hooks/useMediaQuery", () => ({
	useIsDesktop: () => mockIsDesktop(),
	useMediaQuery: () => mockIsDesktop(),
}));

const mockUseAuth = vi.hoisted(() => vi.fn(() => ({ isPlatformAdmin: true })));
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

const mockUseAgents = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/useAgents", () => ({
	useAgents: (scope: unknown, options: unknown) =>
		mockUseAgents(scope, options),
}));

const mockUseOrganizations = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => mockUseOrganizations(),
}));

const mockUseInfiniteAgentRuns = vi.hoisted(() => vi.fn());
const mockUseRerunAgentRun = vi.hoisted(() => vi.fn());

vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: (params: unknown) => mockUseInfiniteAgentRuns(params),
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
const secondPageRun = {
	...run,
	id: "run-26",
	asked: "Triage ticket 428976",
};

beforeEach(() => {
	mockIsDesktop.mockReturnValue(true);
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseAgents.mockReturnValue({
		data: [
			{ id: "agent-1", name: "Service Desk Triage" },
			{ id: "agent-2", name: "Renewal Watch" },
		],
	});
	mockUseOrganizations.mockReturnValue({
		data: [{ id: "org-1", name: "Acme" }],
		isLoading: false,
		isFetching: false,
		error: null,
		refetch: vi.fn(),
	});
	mockUseInfiniteAgentRuns.mockReturnValue({
		data: { pages: [{ items: [run], total: 1 }] },
		isLoading: false,
		isFetching: false,
		isError: false,
		isFetchNextPageError: false,
		hasNextPage: false,
		isFetchingNextPage: false,
		refetch: vi.fn(),
		fetchNextPage: vi.fn(),
	});
	mockUseRerunAgentRun.mockReturnValue({
		mutate: vi.fn(),
		isPending: false,
	});
});

describe("AgentRunsPanel", () => {
	it("keeps the shared pagination summary for a single page", () => {
		renderWithProviders(<AgentRunsPanel />);
		expect(
			screen.getByRole("navigation", { name: "Agent run pages" }),
		).toHaveTextContent("1 run on this page · Page 1");
		expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
		expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
	});
	it("uses the same 25-run Previous and Next pagination as workflows", async () => {
		mockUseInfiniteAgentRuns.mockReturnValue({
			data: {
				pages: [
					{ items: [run], total: 50 },
					{ items: [secondPageRun], total: 50 },
				],
			},
			isLoading: false,
			hasNextPage: false,
			isFetchingNextPage: false,
			fetchNextPage: vi.fn(),
		});

		const { user } = renderWithProviders(
			<Routes>
				<Route path="/history" element={<AgentRunsPanel />} />
			</Routes>,
			{ initialEntries: ["/history?type=agents"] },
		);

		expect(mockUseInfiniteAgentRuns).toHaveBeenCalledWith({ pageSize: 25 });
		expect(screen.getByRole("row", { name: /428950/ })).toBeInTheDocument();
		expect(
			screen.queryByRole("row", { name: /428976/ }),
		).not.toBeInTheDocument();
		expect(
			screen.getByText(/1 run on this page · Page 1/),
		).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Next" }));
		expect(
			screen.queryByRole("row", { name: /428950/ }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("row", { name: /428976/ })).toBeInTheDocument();
		expect(
			screen.getByText(/1 run on this page · Page 2/),
		).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Previous" }));
		expect(screen.getByRole("row", { name: /428950/ })).toBeInTheDocument();
	});

	it("passes supported filters to the agent-runs API wrapper", async () => {
		const { user } = renderWithProviders(
			<Routes>
				<Route path="/history" element={<AgentRunsPanel />} />
			</Routes>,
			{
				initialEntries: [
					"/history?type=agents&agent=agent-2&status=failed&q=acme",
				],
			},
		);

		expect(mockUseInfiniteAgentRuns).toHaveBeenLastCalledWith(
			expect.objectContaining({
				pageSize: 25,
				agentId: "agent-2",
				status: "failed",
				q: "acme",
			}),
		);

		await user.click(screen.getByRole("button", { name: "Clear filters" }));
		expect(mockUseInfiniteAgentRuns).toHaveBeenLastCalledWith({
			pageSize: 25,
		});
	});

	it("constrains the table and progressively collapses secondary columns", () => {
		renderWithProviders(
			<Routes>
				<Route path="/history" element={<AgentRunsPanel />} />
			</Routes>,
			{ initialEntries: ["/history?type=agents"] },
		);

		const table = screen.getAllByRole("table")[0];
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
		expect(
			screen.getByRole("columnheader", { name: "Duration" }),
		).toHaveClass("hidden", "lg:table-cell");
		expect(
			screen.getByRole("columnheader", { name: "Verdict" }),
		).toHaveClass("hidden", "xl:table-cell");
		expect(
			screen.getByRole("columnheader", { name: "Started" }),
		).toHaveClass("hidden", "xl:table-cell");
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
			screen.getByRole("row", {
				name: /Service Desk Triage.*Completed/i,
			}),
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

	it("opens a run row href on ctrl-click from a plain cell", () => {
		const open = vi.spyOn(window, "open").mockImplementation(() => null);
		renderWithProviders(
			<Routes>
				<Route path="/history" element={<AgentRunsPanel />} />
			</Routes>,
			{ initialEntries: ["/history?type=agents"] },
		);

		fireEvent.click(
			screen.getByRole("row", { name: /Service Desk Triage.*Completed/i }),
			{ ctrlKey: true },
		);

		expect(open).toHaveBeenCalledWith(
			"/agents/agent-1/runs/run-1",
			"_blank",
		);
	});
});

it("shows mobile records and preserves the return destination for keyboard navigation", async () => {
	mockIsDesktop.mockReturnValue(false);
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
	expect(screen.queryByRole("table")).not.toBeInTheDocument();
	expect(screen.getByText("Triage ticket 428950")).toBeInTheDocument();
	expect(screen.getByText("Not reviewed")).toBeInTheDocument();
	const link = screen.getByRole("link", { name: "Service Desk Triage" });
	link.focus();
	await user.keyboard("{Enter}");
	expect(screen.getByTestId("navigation-state")).toHaveTextContent(
		'"href":"/history?type=agents"',
	);
});

it("distinguishes initial lookup failure from no runs and offers retry", async () => {
	const refetch = vi.fn();
	mockUseInfiniteAgentRuns.mockReturnValue({
		data: undefined,
		isLoading: false,
		isError: true,
		isFetching: false,
		refetch,
	});
	const { user } = renderWithProviders(<AgentRunsPanel />);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Couldn't load agent runs",
	);
	expect(screen.queryByText("No agent runs yet.")).not.toBeInTheDocument();
	await user.click(
		screen.getByRole("button", { name: "Retry loading agent runs" }),
	);
	expect(refetch).toHaveBeenCalledOnce();
});

it("retains cached runs when refresh fails and retries loading", async () => {
	const refetch = vi.fn();
	mockUseInfiniteAgentRuns.mockReturnValue({
		data: { pages: [{ items: [run], total: 1 }] },
		isLoading: false,
		isError: true,
		isFetching: false,
		refetch,
	});
	const { user } = renderWithProviders(<AgentRunsPanel />);
	expect(
		screen.getByRole("row", { name: /Service Desk Triage.*Completed/i }),
	).toBeVisible();
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Previously loaded records",
	);
	await user.click(screen.getByRole("button", { name: "Retry loading" }));
	expect(refetch).toHaveBeenCalledOnce();
});

it("retains a failed rerun with focused feedback and retries the same source", async () => {
	const mutate = vi
		.fn()
		.mockImplementationOnce((_params, callbacks) => callbacks.onError())
		.mockImplementationOnce((_params, callbacks) =>
			callbacks.onSuccess({ run_id: "retried-run" }),
		);
	mockUseRerunAgentRun.mockReturnValue({ mutate, isPending: false });
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
	await user.click(screen.getByTestId("rerun-run-1"));
	expect(screen.getByRole("alert")).toHaveFocus();
	expect(screen.getByRole("alert")).toHaveTextContent("Service Desk Triage");
	expect(
		screen.getByRole("row", { name: /Service Desk Triage.*Completed/i }),
	).toBeVisible();
	await user.click(screen.getByRole("button", { name: "Retry rerun" }));
	expect(mutate).toHaveBeenNthCalledWith(
		2,
		{ params: { path: { run_id: "run-1" } } },
		expect.any(Object),
	);
	expect(screen.getByTestId("navigation-state")).toHaveTextContent(
		'"href":"/history?type=agents"',
	);
});

it("retries a failed next-page request while retaining the current records", async () => {
	const fetchNextPage = vi.fn().mockResolvedValue({
		data: {
			pages: [
				{ items: [run], total: 26 },
				{ items: [secondPageRun], total: 26 },
			],
		},
	});
	mockUseInfiniteAgentRuns.mockReturnValue({
		data: { pages: [{ items: [run], total: 26 }] },
		isLoading: false,
		isError: true,
		isFetchNextPageError: true,
		isFetchingNextPage: false,
		hasNextPage: true,
		fetchNextPage,
	});
	const { user } = renderWithProviders(<AgentRunsPanel />);
	expect(screen.getByRole("alert")).toHaveTextContent("still on page 1");
	expect(screen.getByRole("row", { name: /428950/ })).toBeVisible();
	expect(
		screen.queryByRole("button", { name: "Retry loading" }),
	).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry next page" }));
	expect(fetchNextPage).toHaveBeenCalledOnce();
});
