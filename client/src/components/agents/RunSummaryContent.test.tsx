import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import type { components } from "@/lib/v1";

import { RunSummaryContent } from "./RunSummaryContent";

type AgentRun = components["schemas"]["AgentRunResponse"];

const run: AgentRun = {
	id: "00000000-0000-0000-0000-000000000001",
	agent_id: "00000000-0000-0000-0000-000000000002",
	trigger_type: "test",
	status: "completed",
	iterations_used: 1,
	tokens_used: 1234,
	asked: "Triage ticket 428950",
	did: "Routed it to Support",
	input: {},
	output: {},
	metadata: { ticket_id: "428950", client: "Contoso" },
	summary_status: "completed",
	created_at: "2026-04-21T10:00:00Z",
	started_at: "2026-04-21T10:00:00Z",
	duration_ms: 2500,
};

describe("RunSummaryContent", () => {
	it("uses a subtle, accessible status indicator and consistent run hierarchy", () => {
		renderWithProviders(<RunSummaryContent run={run} />);

		expect(
			screen.getByRole("img", { name: "Status: Completed" }),
		).toBeInTheDocument();
		expect(screen.getByText("Triage ticket 428950")).toBeInTheDocument();
		expect(screen.getByText("Routed it to Support")).toBeInTheDocument();
		expect(screen.getByText("1,234")).toBeInTheDocument();
		expect(screen.getByText("ticket_id")).toBeInTheDocument();
		expect(screen.getByText("428950")).toBeInTheDocument();
	});

	it("falls back to the run error when no did summary exists", () => {
		renderWithProviders(
			<RunSummaryContent
				run={{ ...run, status: "failed", did: null, error: "boom" }}
			/>,
		);

		expect(
			screen.getByRole("img", { name: "Status: Failed" }),
		).toBeInTheDocument();
		expect(screen.getByText("Error: boom")).toBeInTheDocument();
	});

	it("identifies runs performed as a delegation", () => {
		renderWithProviders(
			<RunSummaryContent
				run={{
					...run,
					parent_run_id:
						"00000000-0000-0000-0000-000000000003",
				}}
			/>,
		);

		expect(screen.getByText("Delegated")).toBeInTheDocument();
	});

	it("prioritizes matching captured data without hiding overflow", () => {
		renderWithProviders(
			<RunSummaryContent
				run={{
					...run,
					metadata: { a: "1", b: "2", c: "3", target: "needle" },
				}}
				highlight="needle"
			/>,
		);

		expect(screen.getByText("target")).toBeInTheDocument();
		expect(screen.getByText("+1")).toBeInTheDocument();
	});
});
