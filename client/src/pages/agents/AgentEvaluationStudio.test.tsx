import { describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";
import { agentPlatform } from "@/services/agentPlatform";
import { AgentEvaluationStudio } from "./AgentEvaluationStudio";
vi.mock("@/hooks/useAgents", () => ({
	useAgent: () => ({ data: { id: "agent", name: "Support" } }),
}));
vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		suites: vi.fn().mockResolvedValue([]),
		execution: vi.fn(),
        suite: vi.fn().mockResolvedValue({id:"suite",agent_id:"agent",status:"draft",version:1}),
        cases: vi.fn().mockResolvedValue([]),
		results: vi.fn().mockResolvedValue([]),
	},
}));
describe("Studio entry", () => {
	it("distinguishes the live baseline from evaluation-only candidates and explains first use", async () => {
		renderWithProviders(
			<Routes>
				<Route
					path="/agents/:id/studio"
					element={<AgentEvaluationStudio />}
				/>
			</Routes>,
			{ initialEntries: ["/agents/agent/studio"] },
		);
		expect(
			screen.getByRole("heading", { name: "Baseline · live Agent" }),
		).toBeVisible();
		expect(
			screen.getByRole("heading", {
				name: "Candidate · evaluation only",
			}),
		).toBeVisible();
		expect(await screen.findByText("Start with a suite")).toBeVisible();
		expect(screen.getByLabelText("Suite")).toHaveValue("");
	});
});

it("does not present candidate execution evidence as a baseline-only comparison", async () => {
	vi.mocked(agentPlatform.execution).mockResolvedValue({
		id: "execution",
		suite_id: "suite",
		candidate_id: "candidate",
	} as never);
	renderWithProviders(
		<Routes>
			<Route
				path="/agents/:id/studio"
				element={<AgentEvaluationStudio />}
			/>
		</Routes>,
		{ initialEntries: ["/agents/agent/studio?suite=suite&execution=execution"] },
	);
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Choose the matching suite and candidate",
	);
});
