import { AgentDetailPage } from "./AgentDetailPage";
/**
 * Tests for AgentDetailPage.
 *
 * Verifies route handling for both edit (`/agents/:id`) and create
 * (`/agents/new`) modes, tab disabled state in create mode, and
 * navigation after create.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { Routes, Route, useLocation } from "react-router-dom";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockUseAgent = vi.fn();
vi.mock("@/hooks/useAgents", async () => {
	const actual =
		await vi.importActual<typeof import("@/hooks/useAgents")>(
			"@/hooks/useAgents",
		);
	return {
		...actual,
		useAgent: (id: string | undefined) => mockUseAgent(id),
	};
});

vi.mock("@/services/agentRuns", () => ({
	useAgentRuns: () => ({ data: { total: 0, runs: [] } }),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: false }),
}));

vi.mock("@/components/agents/SummaryBackfillButton", () => ({
	SummaryBackfillButton: () => null,
}));

// Stub the three tab components to thin probes — we test them in their own
// files. Here we just need to know which tab rendered.
vi.mock("@/components/agents/AgentOverviewTab", () => ({
	AgentOverviewTab: ({ agentId }: { agentId: string }) => (
		<div data-testid="overview-tab">overview-{agentId}</div>
	),
}));

vi.mock("@/components/agents/AgentRunsTab", () => ({
	AgentRunsTab: ({ agentId }: { agentId: string }) => (
		<div data-testid="runs-tab">runs-{agentId}</div>
	),
}));

vi.mock("@/components/agents/AgentSettingsTab", () => ({
	AgentSettingsTab: ({
		mode,
		onCreated,
	}: {
		mode: "create" | "edit";
		onCreated?: (id: string) => void;
	}) => (
		<div data-testid="settings-tab" data-mode={mode}>
			settings-{mode}
			{onCreated ? (
				<button type="button" onClick={() => onCreated("new-agent-id")}>
					trigger-create
				</button>
			) : null}
		</div>
	),
}));

const existingAgent = {
	id: "agent-1",
	name: "Tier-1 Triage",
	description: "Triages tickets",
	system_prompt: "hi",
	channels: ["chat"],
	access_level: "role_based",
	is_active: true,
	tool_ids: [],
	delegated_agent_ids: [],
	role_ids: [],
	knowledge_sources: [],
};

beforeEach(() => {
	mockUseAgent.mockReturnValue({ data: existingAgent, isLoading: false });
});

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

async function renderAtRoute(path: string) {
	function LocationProbe() {
		const loc = useLocation();
		return <div data-testid="location">{loc.pathname}</div>;
	}
	return renderWithProviders(
		<Routes>
			<Route
				path="/agents/:id"
				element={
					<>
						<AgentDetailPage />
						<LocationProbe />
					</>
				}
			/>
			<Route
				path="/agents/new"
				element={
					<>
						<AgentDetailPage />
						<LocationProbe />
					</>
				}
			/>
		</Routes>,
		{ initialEntries: [path] },
	);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("AgentDetailPage — edit mode", () => {
	it("renders the agent name in the header", async () => {
		await renderAtRoute("/agents/agent-1");
		expect(
			screen.getByRole("heading", { name: /tier-1 triage/i }),
		).toBeInTheDocument();
	});

	it("offers the agent Workbench action", async () => {
		await renderAtRoute("/agents/agent-1");

		expect(
			screen.getByRole("link", { name: "Open Workbench" }),
		).toHaveAttribute("href", "/agents/agent-1/quality?tab=tests");
	});

	it("renders the Overview tab by default", async () => {
		await renderAtRoute("/agents/agent-1");
		expect(screen.getByTestId("overview-tab")).toHaveTextContent(
			"overview-agent-1",
		);
	});

	it("renders all three tab triggers as enabled", async () => {
		await renderAtRoute("/agents/agent-1");
		const overview = screen.getByRole("tab", { name: /overview/i });
		const runs = screen.getByRole("tab", { name: /runs/i });
		const settings = screen.getByRole("tab", { name: /settings/i });
		expect(overview).not.toBeDisabled();
		expect(runs).not.toBeDisabled();
		expect(settings).not.toBeDisabled();
	});

	it("switches to the Runs tab when clicked", async () => {
		const { user } = await renderAtRoute("/agents/agent-1");
		await user.click(screen.getByRole("tab", { name: /runs/i }));
		expect(await screen.findByTestId("runs-tab")).toHaveTextContent(
			"runs-agent-1",
		);
	});

	it("switches to the Settings tab and renders edit mode", async () => {
		const { user } = await renderAtRoute("/agents/agent-1");
		await user.click(screen.getByRole("tab", { name: /settings/i }));
		const settings = await screen.findByTestId("settings-tab");
		expect(settings).toHaveAttribute("data-mode", "edit");
	});
});

describe("AgentDetailPage — create mode", () => {
	it("renders the 'New agent' header", async () => {
		await renderAtRoute("/agents/new");
		expect(
			screen.getByRole("heading", { name: /new agent/i }),
		).toBeInTheDocument();
	});

	it("disables the Overview and Runs tabs", async () => {
		await renderAtRoute("/agents/new");
		const overview = screen.getByRole("tab", { name: /overview/i });
		const runs = screen.getByRole("tab", { name: /runs/i });
		expect(overview).toBeDisabled();
		expect(runs).toBeDisabled();
	});

	it("opens with the Settings tab active and renders create mode", async () => {
		await renderAtRoute("/agents/new");
		const settings = await screen.findByTestId("settings-tab");
		expect(settings).toHaveAttribute("data-mode", "create");
	});

	it("navigates to /agents/:newId on successful create", async () => {
		const { user } = await renderAtRoute("/agents/new");
		await user.click(
			screen.getByRole("button", { name: /trigger-create/i }),
		);
		await waitFor(() => {
			expect(screen.getByTestId("location")).toHaveTextContent(
				"/agents/new-agent-id",
			);
		});
	});
});

describe("AgentDetailPage — solution back-nav", () => {
	it("retargets the breadcrumb to the solution when ?from=solution:", async () => {
		await renderAtRoute("/agents/agent-1?from=solution:sol-9");
		const back = screen.getByRole("link", { name: /back to solution/i });
		expect(back).toHaveAttribute("href", "/solutions/sol-9");
	});

	it("keeps the default agents breadcrumb without ?from", async () => {
		await renderAtRoute("/agents/agent-1");
		expect(
			screen.queryByRole("link", { name: /back to solution/i }),
		).not.toBeInTheDocument();
		const back = screen.getByRole("link", { name: /agents/i });
		expect(back).toHaveAttribute("href", "/agents");
	});
});

it("offers read retry without an empty edit form", async () => {
	const refetch = vi.fn();
	mockUseAgent.mockReturnValue({
		data: undefined,
		isLoading: false,
		isError: true,
		refetch,
	});
	const { user } = await renderAtRoute("/agents/agent-1?tab=settings");
	expect(screen.queryByTestId("settings-tab")).not.toBeInTheDocument();
	expect(screen.queryByText("Agent not found.")).not.toBeInTheDocument();
	await user.click(
		screen.getByRole("button", { name: "Retry agent details" }),
	);
	expect(refetch).toHaveBeenCalledOnce();
});
it("retains loaded agent settings during a refresh failure", async () => {
	mockUseAgent.mockReturnValue({
		data: existingAgent,
		isError: true,
		refetch: vi.fn(),
	});
	await renderAtRoute("/agents/agent-1?tab=settings");
	expect(screen.getByTestId("settings-tab")).toBeInTheDocument();
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Previously loaded data is still shown",
	);
});
it("uses the creation form even when a disabled tab is in the URL", async () => {
	await renderAtRoute("/agents/new?tab=runs");
	expect(screen.getByTestId("settings-tab")).toHaveAttribute(
		"data-mode",
		"create",
	);
});

 it("does not show a status-update failure on a fresh New Agent page", async () => {
 await renderAtRoute("/agents/new");
 expect(screen.queryByText("Could not update the agent status. Try again.")).not.toBeInTheDocument();
 });
