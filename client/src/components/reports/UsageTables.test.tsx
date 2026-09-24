import { expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { WorkflowTable } from "./UsageTables";

it("shows the highest CPU workflow first by default", () => {
	renderWithProviders(
		<WorkflowTable
			workflows={[
				{
					workflow_name: "Low CPU",
					execution_count: 1,
					input_tokens: 100,
					output_tokens: 0,
					ai_cost: "5.00",
					cpu_seconds: 1,
					memory_bytes: 1,
				},
				{
					workflow_name: "High CPU",
					execution_count: 1,
					input_tokens: 0,
					output_tokens: 0,
					ai_cost: "0.00",
					cpu_seconds: 100,
					memory_bytes: 1,
				},
			]}
			isLoading={false}
			startDate="2026-09-24"
			endDate="2026-09-24"
			isDemo={false}
		/>,
	);

	const region = screen.getByRole("region", { name: /workflow usage/i });
	expect(region.textContent?.indexOf("High CPU")).toBeLessThan(
		region.textContent?.indexOf("Low CPU") ?? 0,
	);
});
