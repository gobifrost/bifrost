import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
vi.mock("@/services/modelNames", () => ({
	useModelDisplayName: () => (model: string) =>
		model === "claude-haiku-4-5" ? "Claude Haiku 4.5" : model,
}));

import { RunAIUsageCard } from "./RunAIUsageCard";
import type { components } from "@/lib/v1";

type Usage = NonNullable<
	components["schemas"]["AgentRunDetailResponse"]["ai_usage"]
>;

describe("RunAIUsageCard", () => {
	it("leads with cost, peak context, and cache rate for the run", () => {
		renderWithProviders(
			<RunAIUsageCard
				usage={[]}
				summary={{
					cost: "0.0747",
					delegate_cost: "0.0051",
					peak_context_tokens: 18_148,
					cache_hit_rate: 0.14,
				}}
			/>,
		);
		expect(screen.getByText("$0.0747")).toBeInTheDocument();
		expect(screen.getByText("18.1k tokens")).toBeInTheDocument();
		expect(screen.getByText("14%")).toBeInTheDocument();
		expect(
			screen.getByText("Includes $0.005100 from delegated agents."),
		).toBeInTheDocument();
	});

	it("groups this run's calls by model with full names and cache share", () => {
		const model = "a-model-name-that-must-not-be-truncated";
		const usage = [
			{
				model,
				provider: "fixture",
				input_tokens: 100,
				output_tokens: 20,
				cache_read_tokens: 0,
				cost: "1.25",
			},
			{
				model,
				provider: "fixture",
				input_tokens: 300,
				output_tokens: 30,
				cache_read_tokens: 200,
				reasoning_tokens: 12,
				cost: "0.75",
			},
		] as Usage;
		renderWithProviders(<RunAIUsageCard usage={usage} summary={null} />);
		expect(screen.getByText(model)).toBeInTheDocument();
		expect(screen.getByText("2")).toBeInTheDocument();
		expect(screen.getByText("$2.00")).toBeInTheDocument();
		expect(screen.getByText("50%")).toBeInTheDocument();
		// Reasoning is part of output, so it is shown inside the output total.
		expect(screen.getByText("50 (12 reasoning)")).toBeInTheDocument();
	});

	it("shows the catalog display name with the stored id as a tooltip", () => {
		renderWithProviders(
			<RunAIUsageCard
				usage={[
					{
						model: "claude-haiku-4-5",
						provider: "anthropic",
						input_tokens: 10,
						output_tokens: 5,
						cache_read_tokens: 0,
						cache_write_tokens: 0,
						reasoning_tokens: 0,
						timestamp: "2026-09-29T00:00:00Z",
						sequence: 1,
					},
				]}
				summary={null}
			/>,
		);
		expect(screen.getByText("Claude Haiku 4.5")).toHaveAttribute("title", "claude-haiku-4-5");
	});
});
