import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ExecutionAiUsage } from "./ExecutionAiUsage";
import type { components } from "@/lib/v1";

function call(
	provider: string,
	input: number,
): components["schemas"]["AIUsagePublicSimple"] {
	return {
		provider,
		model: "full-model-name-with-long-deployment-identifier",
		input_tokens: input,
		output_tokens: 10,
		cost: "0.01",
		cache_read_tokens: 0,
		cache_write_tokens: 0,
		reasoning_tokens: 0,
		timestamp: "2026-09-06T12:00:00Z",
		sequence: 1,
	};
}

describe("ExecutionAiUsage", () => {
	it("combines repeated calls while keeping providers distinct and full names visible", () => {
		render(
			<ExecutionAiUsage
				usage={[call("one", 100), call("one", 200), call("two", 400)]}
			/>,
		);
		const records = within(
			screen.getByRole("list", { name: "AI usage by model" }),
		).getAllByRole("listitem");
		expect(records).toHaveLength(2);
		expect(within(records[0]).getByText("300")).toBeVisible();
		expect(within(records[0]).getByText("one · 2 calls")).toBeVisible();
		expect(within(records[1]).getByText("400")).toBeVisible();
		expect(
			screen.getAllByText(
				"full-model-name-with-long-deployment-identifier",
			),
		).toHaveLength(2);
	});

	it("keeps totals visible when keyboard users collapse model details", async () => {
		const user = userEvent.setup();
		render(
			<ExecutionAiUsage
				usage={[call("one", 100)]}
				totals={{
					call_count: 1,
					total_input_tokens: 100,
					total_output_tokens: 10,
					total_cost: "0.01",
				}}
			/>,
		);
		const toggle = screen.getByRole("button", { name: "AI usage details" });
		toggle.focus();
		await user.keyboard("{Enter}");
		expect(toggle).toHaveAttribute("aria-expanded", "false");
		expect(screen.queryByRole("list")).not.toBeInTheDocument();
		expect(screen.getByText("Total usage")).toBeVisible();
		expect(screen.getByText("100")).toBeVisible();
		await user.keyboard(" ");
		expect(screen.getByRole("list")).toBeVisible();
	});
});
