import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import type { components } from "@/lib/v1";

import { AgentRunOverviewFooter } from "./AgentRunOverviewFooter";

type Run = components["schemas"]["AgentRunDetailResponse"];

function makeRun(overrides: Partial<Run> = {}): Run {
	return {
		id: "run-1",
		agent_id: "agent-1",
		agent_name: "Triage",
		trigger_type: "manual",
		status: "completed",
		summary_status: "completed",
		iterations_used: 4,
		tokens_used: 500,
		duration_ms: 1500,
		llm_model: "gpt-5.2",
		asked: "Help",
		did: "Helped",
		input: {},
		output: {},
		metadata: {
			ticket: "428950",
			queue: "Help Desk",
			impact: "Single User",
		},
		created_at: "2026-04-20T12:00:00Z",
		steps: [],
		child_run_ids: [],
		child_runs: [],
		ai_usage: [
			{
				model: "gpt-5.2",
				provider: "openai",
				input_tokens: 200,
				output_tokens: 50,
				cache_read_tokens: 0,
				cache_write_tokens: 0,
				timestamp: "2026-04-20T12:00:00Z",
				sequence: 1,
				cost: "0.05",
			},
		],
		ai_totals: {
			call_count: 1,
			total_cache_read_tokens: 0,
			total_cache_write_tokens: 0,
			total_provider_cost: "0.05",
			total_duration_ms: 900,
			total_input_tokens: 200,
			total_output_tokens: 50,
			total_cost: "0.05",
		},
		...overrides,
	};
}

describe("AgentRunOverviewFooter", () => {
	it("keeps usage and metadata collapsed until opened", () => {
		renderWithProviders(
			<AgentRunOverviewFooter
				run={makeRun()}
				agentName="Tier-1 Triage"
				showRegen={false}
				isPlatformAdmin={false}
			/>,
		);

		expect(
			screen.getByRole("button", { name: /ai usage/i }),
		).toHaveAttribute("aria-expanded", "false");
		expect(
			screen.getByRole("button", { name: /run metadata/i }),
		).toHaveAttribute("aria-expanded", "false");
		expect(screen.queryByText("Output tokens")).not.toBeInTheDocument();
		expect(screen.queryByText("Captured data")).not.toBeInTheDocument();
	});

	it("opens one full-width disclosure panel at a time", async () => {
		const { user } = renderWithProviders(
			<AgentRunOverviewFooter
				run={makeRun()}
				agentName="Tier-1 Triage"
				showRegen
				isPlatformAdmin
			/>,
		);

		await user.click(screen.getByRole("button", { name: /ai usage/i }));
		expect(screen.getAllByText("Output tokens").length).toBeGreaterThan(0);
		expect(screen.queryByText("Captured data")).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /run metadata/i }));
		expect(screen.queryByText("Output tokens")).not.toBeInTheDocument();
		expect(screen.getByText("Captured data")).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: /tier-1 triage/i }),
		).toHaveAttribute("href", "/agents/agent-1");
		expect(screen.getByText("ticket:")).toBeInTheDocument();
		expect(
			screen.getAllByRole("button", { name: /Regenerate/ }).length,
		).toBeGreaterThan(0);
	});
});
