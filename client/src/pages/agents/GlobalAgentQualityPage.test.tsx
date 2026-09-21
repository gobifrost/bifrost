import { describe, it, expect, vi, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { GlobalAgentQualityPage } from "./GlobalAgentQualityPage";

const mockSearchFindings = vi.fn();
const mockAgentTests = vi.fn();
const mockReviews = vi.fn();
const mockUseAgents = vi.fn();

vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		searchFindings: (...args: unknown[]) => mockSearchFindings(...args),
		agentTests: (...args: unknown[]) => mockAgentTests(...args),
		reviews: (...args: unknown[]) => mockReviews(...args),
	},
}));

vi.mock("@/hooks/useAgents", () => ({
	useAgents: (...args: unknown[]) => mockUseAgents(...args),
}));

const agents = [
	{ id: "agent-1", name: "Triage" },
	{ id: "agent-2", name: "Billing" },
];

beforeEach(() => {
	mockUseAgents.mockReturnValue({ data: agents, isLoading: false });
	mockSearchFindings.mockReset();
	mockAgentTests.mockReset();
	mockReviews.mockReset();
	mockSearchFindings.mockResolvedValue({
		items: [
			{
				id: "finding-1",
				agent_id: "agent-1",
				description: "Missed escalation",
				status: "open",
				source_kind: "manual",
				finding_kind: "problem",
			},
		],
		total: 1,
	});
	mockAgentTests.mockResolvedValue({
		items: [
			{
				logical_test_id: "test-1",
				name: "Escalates urgent requests",
				enabled: true,
				version: 1,
				origin_suite_id: "suite-1",
				origin_suite_name: "Regression",
				origin_is_default: false,
				case_id: "case-1",
				position: 1,
				repetitions: 1,
				provenance: "manual",
			},
		],
		total: 1,
	});
	mockReviews.mockResolvedValue({
		items: [
			{
				id: "review-1",
				agent_id: "agent-1",
				name: "Weekly service quality",
				status: "active",
				latest_version: 1,
				latest_version_id: "version-1",
				latest_version_created_at: "2026-09-21T00:00:00Z",
				created_at: "2026-09-21T00:00:00Z",
				updated_at: "2026-09-21T00:00:00Z",
			},
		],
		total: 1,
	});
});

function renderPage(entry = "/agents/quality") {
	return renderWithProviders(
		<Routes>
			<Route path="/agents/quality" element={<GlobalAgentQualityPage />} />
			<Route path="/agents/:id/quality" element={<LocationProbe />} />
			<Route path="/agents/:id" element={<div>agent detail</div>} />
		</Routes>,
		{ initialEntries: [entry] },
	);
}

describe("GlobalAgentQualityPage", () => {
	it("searches findings globally and drills into the owning agent workbench", async () => {
		const { user } = renderPage(
			"/agents/quality?collection=findings&search=missed&status=open&kind=problem",
		);

		expect(
			await screen.findByRole("heading", { name: "Agent Workbench" }),
		).toBeVisible();
		expect(document.querySelector("[data-agent-workbench]")).toBeVisible();
		await waitFor(() => {
			expect(mockSearchFindings).toHaveBeenCalledWith(
				expect.objectContaining({
					q: "missed",
					status: "open",
					finding_kind: "problem",
				}),
			);
		});
		expect(await screen.findByText(/Triage/)).toBeVisible();
		expect(screen.getByText(/manual source/i)).toBeVisible();
		expect(
			screen.getByRole("link", { name: "Back to Agents" }),
		).toHaveAttribute("href", "/agents");

		await user.click(
			screen.getByRole("link", { name: /open finding missed escalation/i }),
		);

		expect(await screen.findByTestId("location-probe")).toHaveTextContent(
			"/agents/agent-1/quality?collection=findings&finding=finding-1&selected=findings%3Afinding-1",
		);
	});

	it("uses the shared fleet workbench and defaults to Findings", async () => {
		renderPage();

		expect(document.querySelector("[data-agent-workbench]")).toBeVisible();
		expect(screen.getByRole("combobox", { name: "Workbench collection" })).toHaveTextContent(
			"Findings",
		);
	});

	it("requires an agent before loading tests and preserves the selected test in drill-in", async () => {
		const { user } = renderPage("/agents/quality?collection=tests");
		expect(await screen.findByText(/select an agent/i)).toBeVisible();
		expect(mockAgentTests).not.toHaveBeenCalled();

		await user.click(screen.getByRole("combobox", { name: /agent filter/i }));
		await user.click(screen.getByRole("option", { name: "Triage" }));

		await waitFor(() => {
			expect(mockAgentTests).toHaveBeenCalledWith("agent-1", {
				offset: 0,
				limit: 50,
			});
		});
		await user.click(
			await screen.findByRole("link", {
				name: /open test escalates urgent requests/i,
			}),
		);

		expect(await screen.findByTestId("location-probe")).toHaveTextContent(
			"/agents/agent-1/quality?collection=tests&test=test-1",
		);
	});

	it("loads reviews only after an agent is selected", async () => {
		renderPage("/agents/quality?collection=reviews&agent=agent-1");

		expect(await screen.findByText("Weekly service quality")).toBeVisible();
		expect(mockReviews).toHaveBeenCalledWith({
			agent_id: "agent-1",
			offset: 0,
			limit: 50,
		});
		expect(
			screen.getByRole("link", {
				name: /open review weekly service quality/i,
			}),
		).toHaveAttribute(
			"href",
			"/agents/agent-1/quality?collection=reviews&review=review-1",
		);
	});

	it("keeps the canonical global route before the agent id route", () => {
		const appSource = readFileSync(
			join(process.cwd(), "src/App.tsx"),
			"utf8",
		);

		expect(appSource.indexOf('path="agents/quality"')).toBeGreaterThan(-1);
		expect(appSource.indexOf('path="agents/quality"')).toBeLessThan(
			appSource.indexOf('path="agents/:id"'),
		);
	});
});

function LocationProbe() {
	const location = useLocation();
	return (
		<div data-testid="location-probe">
			{location.pathname}
			{location.search}
		</div>
	);
}
