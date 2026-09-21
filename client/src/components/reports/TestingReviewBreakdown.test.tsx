import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen, within } from "@/test-utils";
import { TestingReviewBreakdown } from "./TestingReviewBreakdown";
import type { components } from "@/lib/v1";

type Breakdown = components["schemas"]["QualityUsageBreakdownResponse"];

function totals(overrides: Partial<Breakdown["overall"]> = {}) {
	return {
		input_tokens: 1000,
		output_tokens: 250,
		cache_read_tokens: 200,
		cache_write_tokens: 50,
		call_count: 4,
		duration_ms: 1250,
		duration_missing_count: 1,
		observed_provider_cost: "1.23",
		estimated_cost: "1.45",
		known_cost: "1.23",
		missing_cost_call_count: 1,
		legacy_call_count: 2,
		...overrides,
	};
}

function coverage(
	overrides: Partial<Breakdown["coverage"]> = {},
): Breakdown["coverage"] {
	return {
		started_attempt_count: 6,
		unobserved_attempt_count: 2,
		missing_cost_call_count: 1,
		unassigned_operation_call_count: 1,
		legacy_coverage_unknown: true,
		legacy_call_count: 2,
		...overrides,
	};
}

function page<T>(items: T[]) {
	return {
		items,
		total_groups: items.length,
		limit: 50,
		offset: 0,
		omitted_group_count: 0,
	};
}

function makeBreakdown(
	overrides: Partial<Breakdown> = {},
): Breakdown {
	return {
		overall: totals(),
		coverage: coverage(),
		by_purpose: page([
			{
				purpose: "synthetic_judge",
				totals: totals({ call_count: 2, known_cost: "0.98" }),
				coverage: coverage({ unobserved_attempt_count: 0 }),
			},
		]),
		by_provider_model: page([
			{
				purpose: "synthetic_judge",
				provider: "openai",
				model: "gpt-5-mini",
				totals: totals({ call_count: 2, known_cost: "0.98" }),
				coverage: coverage({ unobserved_attempt_count: 0 }),
			},
		]),
		by_profile: page([
			{
				purpose: "review",
				profile_id: "profile-1",
				profile_name: "Strict review",
				profile_fingerprint: "fingerprint-1",
				provider: "openai",
				model: "gpt-5-mini",
				totals: totals({ call_count: 1, known_cost: "0.25" }),
				coverage: coverage({ unobserved_attempt_count: 0 }),
			},
		]),
		by_organization: page([
			{
				organization_id: "org-1",
				organization_name: "Acme Corp",
				totals: totals({ call_count: 3, known_cost: "1.10" }),
				coverage: coverage({ unobserved_attempt_count: 1 }),
			},
		]),
		by_operation: page([
			{
				operation_type: "agent_review",
				operation_id: "review-1",
				purpose: "review",
				totals: totals({ call_count: 1, known_cost: "0.25" }),
				coverage: coverage({ unobserved_attempt_count: 0 }),
			},
		]),
		...overrides,
	};
}

describe("TestingReviewBreakdown", () => {
	it("shows partial coverage without treating missing cost as zero", () => {
		renderWithProviders(
			<TestingReviewBreakdown
				data={makeBreakdown()}
				isLoading={false}
				error={null}
				isFetching={false}
				onRetry={vi.fn()}
				filters={{}}
				onFiltersChange={vi.fn()}
			/>,
		);

		expect(screen.getByText("Testing & review breakdown")).toBeVisible();
		expect(screen.getAllByText("Known cost")[0]).toBeVisible();
		expect(screen.getByText("$1.23")).toBeVisible();
		expect(screen.getByText("1 calls missing cost")).toBeVisible();
		expect(screen.getByText(/2 unobserved attempts/)).toBeVisible();
		expect(screen.getAllByText(/Legacy coverage unknown/)[0]).toBeVisible();
		expect(screen.queryByText("$0.00 missing")).not.toBeInTheDocument();
	});

	it("omits cached input ratio when there are no input tokens", () => {
		renderWithProviders(
			<TestingReviewBreakdown
				data={makeBreakdown({
					overall: totals({
						input_tokens: 0,
						cache_read_tokens: 0,
						cache_write_tokens: 50,
					}),
				})}
				isLoading={false}
				error={null}
				isFetching={false}
				onRetry={vi.fn()}
				filters={{}}
				onFiltersChange={vi.fn()}
			/>,
		);

		expect(screen.getByText("Cache read tokens")).toBeVisible();
		expect(screen.getByText("Cache write tokens")).toBeVisible();
		expect(screen.queryByText(/Cached Input:/)).not.toBeInTheDocument();
	});

	it("renders expandable grouping tables", async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<TestingReviewBreakdown
				data={makeBreakdown()}
				isLoading={false}
				error={null}
				isFetching={false}
				onRetry={vi.fn()}
				filters={{}}
				onFiltersChange={vi.fn()}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /Provider \/ model/ }));
		const providerTable = screen.getByRole("region", {
			name: "Provider / model breakdown",
		});
		expect(within(providerTable).getByText("openai / gpt-5-mini")).toBeVisible();
		expect(within(providerTable).getByText("synthetic_judge")).toBeVisible();

		await user.click(screen.getByRole("button", { name: /Profile/ }));
		expect(
			within(screen.getByRole("region", { name: "Profile breakdown" })).getByText(
				"Strict review",
			),
		).toBeVisible();

		await user.click(screen.getByRole("button", { name: /Operation/ }));
		expect(
			within(
				screen.getByRole("region", { name: "Operation breakdown" }),
			).getByText("agent_review: review-1"),
		).toBeVisible();
	});

	it("offers retry when the breakdown fails", async () => {
		const user = userEvent.setup();
		const retry = vi.fn();
		renderWithProviders(
			<TestingReviewBreakdown
				data={undefined}
				isLoading={false}
				error={new Error("failed")}
				isFetching={false}
				onRetry={retry}
				filters={{}}
				onFiltersChange={vi.fn()}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Retry breakdown" }));

		expect(retry).toHaveBeenCalledTimes(1);
	});
});
