import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";
import { AgentDebuggerPage } from "./AgentDebuggerPage";
import { agentPlatform } from "@/services/agentPlatform";
vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));
vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: () => ({ data: undefined }) },
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		snapshot: vi.fn(),
		tree: vi.fn(),
		timeline: vi.fn(),
		checkpoints: vi.fn(),
	},
}));
describe("run debugger", () => {
	it("retains a clear legacy empty state and original-run navigation", async () => {
		vi.mocked(agentPlatform.snapshot).mockResolvedValue({
			run_id: "run",
			status: "completed",
			attempt: 0,
			checkpoint_sequence: 0,
		});
		vi.mocked(agentPlatform.tree).mockResolvedValue({
			requested_run_id: "run",
			root_run_id: "run",
			total_runs: 1,
			truncated: false,
			root: {
				run_id: "run",
				agent_id: "agent",
				agent_name: "Support",
				status: "completed",
				depth: 0,
				attempt: 0,
			},
		});
		vi.mocked(agentPlatform.timeline).mockResolvedValue({
			run_id: "run",
			include_descendants: false,
			entries: [],
		});
		vi.mocked(agentPlatform.checkpoints).mockResolvedValue({
			run_id: "run",
			checkpoints: [],
		});
		renderWithProviders(
			<Routes>
				<Route
					path="/agents/:agentId/runs/:runId/debug"
					element={<AgentDebuggerPage />}
				/>
			</Routes>,
			{ initialEntries: ["/agents/agent/runs/run/debug"] },
		);
		expect(
			await screen.findByText(/older run has no immutable snapshot/),
		).toBeVisible();
		expect(
			screen.getByRole("link", { name: "Back to run" }),
		).toHaveAttribute("href", "/agents/agent/runs/run");
		expect(screen.getByLabelText("Delegated runs")).toBeVisible();
	});
});
