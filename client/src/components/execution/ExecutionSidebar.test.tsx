/**
 * Component tests for ExecutionSidebar.
 *
 * Despite the name, this component is a details panel (not a list). It
 * renders a stack of cards whose visibility is gated by a handful of flags:
 *   - extrasOnly → hide the Details and Input Parameters sections
 *   - executionContext → show the Execution Context card (admin)
 *   - isPlatformAdmin + isComplete → show Runtime Variables card
 *   - Usage card when (admin compute metrics) OR aiUsage has entries
 *
 * The status badge and error display intentionally do NOT live here —
 * status belongs to the page header and the error banner leads the main
 * content column (see ExecutionDetails).
 *
 * We stub the child renderers that do heavy lifting (PrettyInputDisplay,
 * VariablesTreeView) so the assertions target the sidebar's own structure.
 */

import { describe, it, expect, vi } from "vitest";
import type { ComponentProps } from "react";
import { renderWithProviders, screen } from "@/test-utils";
import { ExecutionSidebar } from "./ExecutionSidebar";

vi.mock("./PrettyInputDisplay", () => ({
	PrettyInputDisplay: ({
		inputData,
	}: {
		inputData?: Record<string, unknown>;
	}) => <div aria-label="pretty-input-stub">{JSON.stringify(inputData)}</div>,
}));

vi.mock("@/components/ui/variables-tree-view", () => ({
	VariablesTreeView: ({ data }: { data: Record<string, unknown> }) => (
		<div aria-label="variables-tree-stub">{JSON.stringify(data)}</div>
	),
}));

function render(
	props: Partial<ComponentProps<typeof ExecutionSidebar>> = {},
) {
	const baseProps = {
		executedByName: "alice",
		orgName: "Acme",
		startedAt: "2026-04-20T12:00:00Z",
		completedAt: null,
		inputData: { a: 1 },
		isComplete: true,
		isPlatformAdmin: false,
		isLoading: false,
		...props,
	};
	return renderWithProviders(<ExecutionSidebar {...baseProps} />);
}

describe("ExecutionSidebar — default layout", () => {
	it("renders Details and Input Parameters cards", async () => {
		await render();
		expect(screen.getByText(/^details$/i)).toBeInTheDocument();
		expect(screen.getByText(/input parameters/i)).toBeInTheDocument();
	});

	it("renders executor and scope", async () => {
		await render({ orgName: "Acme" });
		expect(screen.getByText("alice")).toBeInTheDocument();
		expect(screen.getByText("Acme")).toBeInTheDocument();
	});

	it("falls back to 'Global' when orgName is empty", async () => {
		await render({ orgName: null });
		expect(screen.getByText("Global")).toBeInTheDocument();
	});

	it("shows the Completed row only when completedAt is set", async () => {
		const { rerender } = await render({ completedAt: null });
		expect(screen.queryByText(/^completed$/i)).not.toBeInTheDocument();

		rerender(
			<ExecutionSidebar
				executedByName="alice"
				orgName="Acme"
				startedAt="2026-04-20T12:00:00Z"
				completedAt="2026-04-20T12:01:00Z"
				inputData={{}}
				isComplete={true}
				isPlatformAdmin={false}
				isLoading={false}
			/>,
		);
		expect(screen.getByText(/^completed$/i)).toBeInTheDocument();
	});

	it("shows a Duration row when durationMs is provided", async () => {
		await render({ durationMs: 5000 });
		expect(screen.getByText(/^duration$/i)).toBeInTheDocument();
		expect(screen.getByText("5.00s")).toBeInTheDocument();
	});

	it("exposes the absolute started time via a title tooltip", async () => {
		await render();
		const started = screen.getByText(/^started$/i)
			.nextElementSibling as HTMLElement;
		expect(started).toHaveAttribute("title");
	});
});

describe("ExecutionSidebar — extrasOnly mode", () => {
	it("hides details / input when extrasOnly is true", async () => {
		await render({ extrasOnly: true });
		expect(screen.queryByText(/^details$/i)).not.toBeInTheDocument();
		expect(
			screen.queryByText(/input parameters/i),
		).not.toBeInTheDocument();
	});
});

describe("ExecutionSidebar — admin-only sections", () => {
	it("renders Execution Context when executionContext is present", async () => {
		await render({
			executionContext: { org_id: "o1", email: "a@b.com" },
		});
		expect(screen.getByText(/execution context/i)).toBeInTheDocument();
		expect(screen.getByLabelText("variables-tree-stub")).toBeInTheDocument();
	});

	it("shows Runtime Variables card for admins after completion", async () => {
		await render({
			isPlatformAdmin: true,
			isComplete: true,
			variablesData: { counter: 3 },
		});
		expect(screen.getByText(/runtime variables/i)).toBeInTheDocument();
	});

	it("shows 'No variables captured' when admin variablesData is empty", async () => {
		await render({
			isPlatformAdmin: true,
			isComplete: true,
			variablesData: {},
		});
		expect(screen.getByText(/no variables captured/i)).toBeInTheDocument();
	});

	it("hides Runtime Variables card when not an admin", async () => {
		await render({
			isPlatformAdmin: false,
			isComplete: true,
			variablesData: { counter: 3 },
		});
		expect(screen.queryByText(/runtime variables/i)).not.toBeInTheDocument();
	});
});

describe("ExecutionSidebar — Usage card", () => {
	it("renders compute metrics for admins when memory/cpu are present", async () => {
		await render({
			isPlatformAdmin: true,
			isComplete: true,
			peakMemoryBytes: 1024 * 1024 * 200,
			cpuTotalSeconds: 1.23456,
		});
		expect(screen.getByText(/^usage$/i)).toBeInTheDocument();
		expect(screen.getByText(/memory/i)).toBeInTheDocument();
		expect(screen.getByText(/cpu time/i)).toBeInTheDocument();
		// CPU time is fixed to 3 decimals.
		expect(screen.getByText(/1\.235s/)).toBeInTheDocument();
	});

	it("renders AI Usage rows and totals for everyone when aiUsage has entries", async () => {
		await render({
			isPlatformAdmin: false,
			isComplete: true,
			aiUsage: [
				{
					provider: "anthropic",
					model: "claude-3",
					input_tokens: 100,
					output_tokens: 50,
					cost: "0.0042",
				},
				{
					provider: "anthropic",
					model: "claude-3",
					input_tokens: 200,
					output_tokens: 75,
					cost: "0.0084",
				},
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
			] as any,
			aiTotals: {
				call_count: 2,
				total_input_tokens: 300,
				total_output_tokens: 125,
				total_cost: "0.0126",
			},
		});
		expect(screen.getByText(/ai usage/i)).toBeInTheDocument();
		// Pluralised call-count badge.
		expect(screen.getByText(/2 calls/i)).toBeInTheDocument();
		// The grouped-by-model row shows combined tokens (100+200 in column).
		expect(screen.getByText("claude-3")).toBeInTheDocument();
	});

	it("singularises the AI call badge when there is exactly one call", async () => {
		await render({
			isPlatformAdmin: false,
			isComplete: true,
			aiUsage: [
				{
					provider: "anthropic",
					model: "claude-3",
					input_tokens: 10,
					output_tokens: 5,
					cost: "0.0001",
				},
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
			] as any,
			aiTotals: {
				call_count: 1,
				total_input_tokens: 10,
				total_output_tokens: 5,
				total_cost: "0.0001",
			},
		});
		expect(screen.getByText(/1 call$/i)).toBeInTheDocument();
	});

	it("hides the Usage card when there's no data to show", async () => {
		await render({
			isPlatformAdmin: false,
			isComplete: true,
			aiUsage: [],
		});
		expect(screen.queryByText(/^usage$/i)).not.toBeInTheDocument();
	});
});
