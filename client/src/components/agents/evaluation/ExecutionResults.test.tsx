import { describe, expect, it } from "vitest";
import { fireEvent } from "@testing-library/react";
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
		).toHaveAttribute("href", "/agents/agent/runs/child?tab=activity&sequence=17");
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

it("shows failed assertions first with expected and actual inline", () => {
	renderWithProviders(
		<ExecutionResults
			results={[
				{
					id: "result",
					execution_id: "execution",
					case_id: "case",
					case_version: 1,
					repetition_index: 0,
				status: "failed",
				baseline_run_id: "baseline",
				candidate_run_id: "candidate",
				comparison: {
					usage: {
						baseline: { cost_usd: 0.01 },
						candidate: { cost_usd: 0.02 },
					},
				},
				assertion_results: [
						{
							passed: true,
							code: "output_path",
							label: "Answer format",
							side: "baseline",
							expected: "ok",
							actual: "ok",
						},
						{
							passed: false,
							code: "tool_called",
							label: "Lookup used",
							side: "candidate",
							expected: "lookup",
							actual: "answer",
							detail: "answered directly",
						},
					],
				},
			]}
			cases={[]}
			agentId="agent"
		/>,
	);
	const items = screen.getAllByRole("listitem");
	expect(items[0]).toHaveTextContent("Lookup used");
	expect(screen.getByText("Expected:")).toBeVisible();
	expect(screen.getByText("lookup")).toBeVisible();
	expect(screen.getByText("answer")).toBeVisible();
	expect(screen.getAllByText("Live agent").length).toBeGreaterThan(0);
	fireEvent.click(screen.getByText("Run details", { selector: "summary" }));
	expect(
		screen.getByRole("columnheader", { name: "Live agent" }),
	).toBeInTheDocument();
	expect(
		screen.getByRole("link", { name: "Inspect live-agent run" }),
	).toHaveAttribute("href", "/agents/agent/runs/baseline");
});

it("hides comparison counts on a live-only run and groups details once", () => {
	renderWithProviders(
		<ExecutionResults
			results={[
				{
					id: "result",
					execution_id: "execution",
					case_id: "case",
					case_version: 1,
					repetition_index: 0,
					status: "failed",
					baseline_run_id: "baseline",
					comparison: {
						usage: { baseline: { cost_usd: 0.01 } },
					},
					assertion_results: [
						{
							passed: false,
							code: "tool_called",
							label: "Lookup used",
							side: "baseline",
							expected: "lookup",
							actual: "answer",
						},
					],
				},
			]}
			cases={[]}
			agentId="agent"
		/>,
	);
	expect(screen.queryByText("regressions")).not.toBeInTheDocument();
	expect(screen.queryByText("improvements")).not.toBeInTheDocument();
	expect(screen.queryByText("unchanged failures")).not.toBeInTheDocument();
	fireEvent.click(screen.getByText("Run details", { selector: "summary" }));
	expect(screen.getByText("Usage", { selector: "h5" })).toBeVisible();
	expect(
		screen.getByText("Raw evidence", { selector: "h5" }),
	).toBeVisible();
	expect(
		screen.queryByText("Tool-call changes", { selector: "h5" }),
	).not.toBeInTheDocument();
});

it("keeps hash-only evidence reachable after expected behavior", () => {
	renderWithProviders(
		<ExecutionResults
			results={[
				{
					id: "result",
					execution_id: "execution",
					case_id: "case",
					case_version: 1,
					repetition_index: 0,
					status: "passed",
					baseline_run_id: "baseline",
					simulator_state_hash: "abc123",
					assertion_results: [
						{
							passed: true,
							code: "terminal_status",
							label: "Run completes",
							side: "baseline",
						},
					],
				},
			]}
			cases={[]}
			agentId="agent"
		/>,
	);
	expect(screen.queryByText("regressions")).not.toBeInTheDocument();
	const details = screen.getByText("Run details", {
		selector: "summary",
	});
	expect(details).toBeVisible();
	const expectations = screen.getByText("Expected behavior", {
		selector: "h4",
	});
	expect(
		expectations.compareDocumentPosition(details) &
			Node.DOCUMENT_POSITION_FOLLOWING,
	).toBeTruthy();
	fireEvent.click(details);
	expect(screen.getByText(/abc123/)).toBeVisible();
});
