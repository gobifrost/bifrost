import { describe, expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ExecutionResults } from "./ExecutionResults";
import type { components } from "@/lib/v1";
describe("execution evidence", () => {
	it("distinguishes missing cache evidence from measured zero and links exact run sequences", () => {
		const result: components["schemas"]["EvaluationResultPublic"] = {
			id: "result",
			execution_id: "execution",
			case_id: "case",
			case_version: 1,
			repetition_index: 0,
			status: "passed",
			baseline_run_id: "baseline",
			candidate_run_id: "candidate",
			comparison: {
				usage: {
					baseline: {
						cache_hit_fraction: null,
						cache_read_tokens: 0,
					},
					candidate: { cache_hit_fraction: 0 },
				},
			},
			assertion_results: [
				{
					passed: true,
					code: "tool_called",
					evidence_references: [{ run_id: "child", sequence: 17 }],
				},
			],
		};
		renderWithProviders(
			<ExecutionResults results={[result]} cases={[]} agentId="agent" />,
		);
		expect(screen.getByText("0.00%")).toBeInTheDocument();
		expect(screen.getAllByText("Not recorded").length).toBeGreaterThan(0);
		expect(
			screen.getByRole("link", {
				name: "Sequence 17 · child",
				hidden: true,
			}),
		).toHaveAttribute("href", "/agents/agent/runs/child/debug?sequence=17");
		expect(
			screen.getByText(/Automatic summaries excluded/),
		).toBeInTheDocument();
	});
});

it("does not mark undispatched assertion definitions as failed", () => {
	renderWithProviders(
		<ExecutionResults
			results={[
				{
					id: "pending",
					execution_id: "execution",
					case_id: "case",
					case_version: 1,
					repetition_index: 0,
					status: "running",
					assertion_results: [
						{ definition: { type: "no_real_tools" } },
					],
				},
			]}
			cases={[]}
			agentId="agent"
		/>,
	);
	expect(screen.getByText("pending")).toBeVisible();
	expect(
		screen.queryByText("failed", { exact: true }),
	).not.toBeInTheDocument();
});
