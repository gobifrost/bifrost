import { describe, expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { RunAIUsageCard } from "./RunAIUsageCard";
import type { components } from "@/lib/v1";

describe("RunAIUsageCard", () => {
	it("groups model calls while preserving full model names and server totals", () => {
		const model = "a-model-name-that-must-not-be-truncated";
		const usage = [
			{
				model,
				provider: "fixture",
				input_tokens: 100,
				output_tokens: 20,
				cost: "1.25",
			},
			{
				model,
				provider: "fixture",
				input_tokens: 200,
				output_tokens: 30,
				cost: "0.75",
			},
		] as components["schemas"]["AgentRunDetailResponse"]["ai_usage"];
		renderWithProviders(
			<RunAIUsageCard
				usage={usage!}
				totals={{
					call_count: 3,
					total_cache_read_tokens: 0,
					total_cache_write_tokens: 0,
					total_provider_cost: "3.00",
					total_duration_ms: 100,
					total_input_tokens: 400,
					total_output_tokens: 60,
					total_cost: "3.00",
				}}
			/>,
		);
		expect(screen.getByText(model)).toBeInTheDocument();
		expect(screen.getByText("300")).toBeInTheDocument();
		expect(screen.getByText("50")).toBeInTheDocument();
		expect(screen.getByText("400")).toBeInTheDocument();
		expect(screen.getByText("60")).toBeInTheDocument();
		expect(screen.getAllByText("Input tokens")).toHaveLength(2);
	});
});

it("excludes automatic summaries from runtime totals and cache reuse", () => {
	const usage = [
		{
			model: "runtime-model",
			provider: "fixture",
			input_tokens: 100,
			output_tokens: 10,
			cache_read_tokens: 25,
			cache_write_tokens: 5,
			sequence: 1,
			cost: "1",
			timestamp: "2026-09-19",
		},
		{
			model: "summary-model",
			provider: "fixture",
			input_tokens: 999,
			output_tokens: 999,
			cache_read_tokens: 999,
			cache_write_tokens: 999,
			sequence: 0,
			cost: "9",
			timestamp: "2026-09-19",
		},
	];
	renderWithProviders(<RunAIUsageCard usage={usage} totals={null} />);
	expect(screen.queryByText("summary-model")).not.toBeInTheDocument();
	expect(screen.getByText("25.00%")).toBeInTheDocument();
	expect(screen.getByText("25")).toBeInTheDocument();
	expect(screen.queryByText("999")).not.toBeInTheDocument();
});
