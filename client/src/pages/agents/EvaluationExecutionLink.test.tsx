import { describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";
import { EvaluationExecutionLink } from "./EvaluationExecutionLink";
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		execution: vi
			.fn()
			.mockResolvedValue({
				baseline_agent_id: "agent",
				suite_id: "suite",
				candidate_id: "candidate",
			}),
	},
}));
function Destination() {
	return <p>{useLocation().search}</p>;
}
describe("execution notification link", () => {
	it("restores the exact Agent, suite, candidate and execution", async () => {
		renderWithProviders(
			<Routes>
				<Route
					path="/agent-evaluations/executions/:executionId"
					element={<EvaluationExecutionLink />}
				/>
				<Route path="/agents/agent/quality" element={<Destination />} />
			</Routes>,
			{ initialEntries: ["/agent-evaluations/executions/execution"] },
		);
		expect(
			await screen.findByText(
				"?tab=changes&suite=suite&candidate=candidate&execution=execution",
			),
		).toBeVisible();
	});
});
