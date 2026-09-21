import { describe, it, expect, vi, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { GlobalAgentQualityPage } from "./GlobalAgentQualityPage";

const mockSearchFindings = vi.fn();
const mockAgentTests = vi.fn();
const mockLatestAgentTests = vi.fn();
const mockReviews = vi.fn();
const mockUseAgents = vi.fn();

vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		searchFindings: (...args: unknown[]) => mockSearchFindings(...args),
		agentTests: (...args: unknown[]) => mockAgentTests(...args),
		latestAgentTests: (...args: unknown[]) => mockLatestAgentTests(...args),
		reviews: (...args: unknown[]) => mockReviews(...args),
	},
}));

vi.mock("@/hooks/useAgents", () => ({
	useAgents: (...args: unknown[]) => mockUseAgents(...args),
	useAgent: () => ({
		data: null,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	}),
}));

vi.mock("@/services/agentRuns", () => ({
	useInfiniteAgentRuns: () => ({
		data: undefined,
		isLoading: false,
		isError: false,
		refetch: vi.fn(),
	}),
}));

const agents = [
	{ id: "agent-1", name: "Triage" },
	{ id: "agent-2", name: "Billing" },
];

beforeEach(() => {
	mockUseAgents.mockReturnValue({ data: agents, isLoading: false });
	mockSearchFindings.mockReset();
	mockAgentTests.mockReset();
	mockLatestAgentTests.mockReset();
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
			{
				id: "finding-2",
				agent_id: "agent-2",
				description: "Billing missed detail is incomplete",
				status: "open",
				source_kind: "review",
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
	mockLatestAgentTests.mockResolvedValue({
		items: [
			{
				logical_test_id: "test-1",
				version: 1,
				origin_suite_name: "Regression",
				simulation: {
					execution_id: "execution-1",
					case_version: 1,
					profile_id: null,
					candidate_id: null,
					status: "passed",
					created_at: "2026-09-21T00:00:00Z",
				},
				recorded: null,
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
			<Route
				path="/agents/quality"
				element={
					<>
						<GlobalAgentQualityPage />
						<LocationProbe />
					</>
				}
			/>
			<Route path="/agents/:id/quality" element={<LocationProbe />} />
			<Route path="/agents/:id" element={<div>agent detail</div>} />
		</Routes>,
		{ initialEntries: [entry] },
	);
}

describe("GlobalAgentQualityPage", () => {
	it("keeps a selected fleet Finding in the attached inspector", async () => {
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
			screen.getByRole("button", { name: /missed escalation/i }),
		);

		expect(
			await screen.findByRole("heading", { name: "Finding details" }),
		).toBeVisible();
		expect(
			screen.getByRole("list", { name: "Findings collection" }),
		).toBeVisible();
		expect(screen.getByTestId("location-probe")).toHaveTextContent(
			"/agents/quality?collection=findings&search=missed&status=open&kind=problem&selected=findings%3Afinding-1&finding=finding-1",
		);
		expect(
			screen.getByText("Billing missed detail is incomplete"),
		).toBeVisible();
	});

	it("uses the shared fleet workbench and defaults to Findings", async () => {
		renderPage();

		expect(document.querySelector("[data-agent-workbench]")).toBeVisible();
		expect(
			await screen.findByLabelText("Workbench collections"),
		).toBeVisible();
		expect(document.querySelector("[data-workspace-header]")).toBeVisible();
		expect(
			screen.getByRole("combobox", { name: "Workbench collection" }),
		).toHaveTextContent("Findings");
		expect(
			await screen.findByRole("list", { name: "Findings collection" }),
		).toBeVisible();
		expect(screen.getAllByRole("listitem")).toHaveLength(2);
	});

	it("filters fleet Tests by one agent and inspects the selected test in place", async () => {
		const { user } = renderPage("/agents/quality?collection=tests");
		expect(await screen.findByText(/choose an agent/i)).toBeVisible();
		expect(mockAgentTests).not.toHaveBeenCalled();

		await user.click(
			screen.getByRole("combobox", { name: /agent filter/i }),
		);
		await user.click(screen.getByRole("option", { name: "Triage" }));

		await waitFor(() => {
			expect(mockAgentTests).toHaveBeenCalledWith("agent-1", {
				offset: 0,
				limit: 50,
			});
			expect(mockLatestAgentTests).toHaveBeenCalledWith("agent-1", {
				offset: 0,
				limit: 50,
			});
		});
		await user.click(
			await screen.findByRole("button", {
				name: /escalates urgent requests/i,
			}),
		);

		expect(
			await screen.findByRole("heading", {
				name: "Escalates urgent requests",
			}),
		).toBeVisible();
		expect(screen.getAllByText(/Simulation: passed/i)).toHaveLength(2);
		expect(screen.getByRole("listitem")).toHaveTextContent("Triage");
	});

	it("loads reviews for the selected agent without leaving the fleet workspace", async () => {
		const { user } = renderPage(
			"/agents/quality?collection=reviews&agent=agent-1",
		);

		expect(await screen.findByText("Weekly service quality")).toBeVisible();
		expect(mockReviews).toHaveBeenCalledWith({
			agent_id: "agent-1",
			offset: 0,
			limit: 50,
		});
		await user.click(
			screen.getByRole("button", { name: /weekly service quality/i }),
		);
		expect(
			await screen.findByRole("heading", { name: "Review details" }),
		).toBeVisible();
		expect(screen.getByText(/Triage · Version 1 · active/i)).toBeVisible();
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
