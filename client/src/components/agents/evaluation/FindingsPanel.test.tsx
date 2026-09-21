import { describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { FindingsPanel } from "./FindingsPanel";
import { agentPlatform } from "@/services/agentPlatform";

vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		findings: vi.fn(),
		createFinding: vi.fn(),
		updateFinding: vi.fn(),
	},
}));

function makeFinding(status = "open") {
	return {
		id: "finding-1",
		agent_id: "agent",
		org_id: null,
		status,
		description: "Routes without confirming.",
		expected_behavior: "Ask first.",
		source_kind: "run",
		finding_kind: "problem",
		source_run_id: "run-1",
		source_sequence: 7,
		external_ref: null,
		linked_case_ids: [],
		created_by: null,
		created_at: "2026-09-19T00:00:00Z",
		updated_at: "2026-09-19T00:00:00Z",
	};
}

describe("FindingsPanel", () => {
	it("lists open findings with run evidence links", async () => {
		vi.mocked(agentPlatform.findings).mockResolvedValue([makeFinding()]);
		renderWithProviders(
			<FindingsPanel agentId="agent" onNewTest={() => {}} />,
			{ initialEntries: ["/agents/agent/quality"] },
		);
		expect(
			await screen.findByText("Routes without confirming."),
		).toBeVisible();
		expect(
			screen.getByRole("link", { name: /Run run-1/ }),
		).toHaveAttribute(
			"href",
			"/agents/agent/runs/run-1?tab=activity&sequence=7",
		);
	});

	it("records a manual finding and dismisses without a test", async () => {
		vi.mocked(agentPlatform.findings).mockResolvedValue([makeFinding()]);
		vi.mocked(agentPlatform.createFinding).mockResolvedValue({
			...makeFinding(),
			id: "finding-2",
		});
		vi.mocked(agentPlatform.updateFinding).mockResolvedValue(makeFinding());
		const { user } = renderWithProviders(
			<FindingsPanel agentId="agent" onNewTest={() => {}} />,
			{ initialEntries: ["/agents/agent/quality"] },
		);
		await user.click(
			await screen.findByRole("button", { name: "Record finding" }),
		);
		await user.type(
			screen.getByLabelText("Observed problem"),
			"Answers before lookup.",
		);
		await user.click(
			await screen.findByRole("button", { name: "Record finding" }),
		);
		expect(agentPlatform.createFinding).toHaveBeenCalledWith({
			agent_id: "agent",
			description: "Answers before lookup.",
			expected_behavior: null,
			source_kind: "manual",
			finding_kind: "problem",
			source_run_id: null,
			source_sequence: null,
			external_ref: null,
		});
		await user.click(
			await screen.findByRole("button", {
				name: "Dismiss without test",
			}),
		);
		expect(agentPlatform.updateFinding).toHaveBeenCalledWith(
			"finding-1",
			{ status: "dismissed" },
		);
	});

	it("filters the loaded findings honestly by search", async () => {
		vi.mocked(agentPlatform.findings).mockResolvedValue([
			makeFinding(),
			{
				...makeFinding(),
				id: "finding-2",
				description: "Ignores the budget cap.",
				expected_behavior: "Respect limits.",
				source_kind: "manual",
				finding_kind: "problem",
				source_run_id: null,
				source_sequence: null,
			},
		]);
		const { user } = renderWithProviders(
			<FindingsPanel agentId="agent" onNewTest={() => {}} />,
			{ initialEntries: ["/agents/agent/quality"] },
		);
		expect(
			await screen.findByText("Routes without confirming."),
		).toBeVisible();
		expect(screen.getByText("Ignores the budget cap.")).toBeVisible();
		await user.type(screen.getByLabelText("Search findings"), "budget");
		await waitFor(() =>
			expect(
				screen.queryByText("Routes without confirming."),
			).not.toBeInTheDocument(),
		);
		expect(screen.getByText("Ignores the budget cap.")).toBeVisible();
	});
});
