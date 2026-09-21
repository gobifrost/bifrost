import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen } from "@/test-utils";
import { UsageReports } from "./UsageReports";

const mockUseAuth = vi.fn();
const mockUseOrganizations = vi.fn();
const mockUseUsageReport = vi.fn();
const mockUseUsageBreakdown = vi.fn();
const mockUsageCharts = vi.fn();
const mockTestingReviewBreakdown = vi.fn();
const mockWorkflowTable = vi.fn();
const mockConversationTable = vi.fn();
const mockAgentTable = vi.fn();
const mockOrganizationTable = vi.fn();
const mockKnowledgeStorageTable = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (...args: unknown[]) => mockUseOrganizations(...args),
}));

vi.mock("@/services/usage", () => ({
	useUsageReport: (...args: unknown[]) => mockUseUsageReport(...args),
	useUsageBreakdown: (...args: unknown[]) => mockUseUsageBreakdown(...args),
}));

vi.mock("@/components/layout/ListPageHeader", () => ({
	ListPageHeader: ({
		title,
		description,
		actions,
	}: {
		title: string;
		description?: string;
		actions?: ReactNode;
	}) => (
		<header>
			<h1>{title}</h1>
			{description && <p>{description}</p>}
			{actions}
		</header>
	),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		value,
		onChange,
	}: {
		value: string | null | undefined;
		onChange: (value: string | null | undefined) => void;
	}) => (
		<div>
			<p>Selected organization: {value ?? "all"}</p>
			<button type="button" onClick={() => onChange("org-1")}>
				Choose Acme
			</button>
			<button type="button" onClick={() => onChange(null)}>
				Choose global
			</button>
		</div>
	),
}));

vi.mock("@/components/ui/date-range-picker", () => ({
	DateRangePicker: () => <div>Report date range picker</div>,
}));

vi.mock("@/components/reports/UsageCharts", () => ({
	UsageCharts: (props: unknown) => {
		mockUsageCharts(props);
		return <section aria-label="usage chart">Usage chart</section>;
	},
}));

vi.mock("@/components/reports/TestingReviewBreakdown", () => ({
	TestingReviewBreakdown: (props: unknown) => {
		mockTestingReviewBreakdown(props);
		return <section aria-label="testing review breakdown">Testing review breakdown</section>;
	},
}));

vi.mock("@/components/reports/UsageTables", () => ({
	WorkflowTable: (props: {
		workflows?: Array<{ workflow_name: string }>;
	}) => {
		mockWorkflowTable(props);
		return (
			<section aria-label="workflow usage">
				{props.workflows?.map((workflow) => (
					<p key={workflow.workflow_name}>{workflow.workflow_name}</p>
				)) ?? "No workflow rows"}
			</section>
		);
	},
	ConversationTable: (props: {
		conversations?: Array<{ conversation_title: string | null }>;
	}) => {
		mockConversationTable(props);
		return (
			<section aria-label="conversation usage">
				{props.conversations?.map((conversation) => (
					<p key={conversation.conversation_title ?? "untitled"}>
						{conversation.conversation_title ?? "Untitled"}
					</p>
				)) ?? "No conversation rows"}
			</section>
		);
	},
	AgentTable: (props: { agents?: Array<{ agent_name: string }> }) => {
		mockAgentTable(props);
		return (
			<section aria-label="agent usage">
				{props.agents?.map((agent) => (
					<p key={agent.agent_name}>{agent.agent_name}</p>
				)) ?? "No agent rows"}
			</section>
		);
	},
	OrganizationTable: (props: {
		organizations?: Array<{ organization_name: string }>;
	}) => {
		mockOrganizationTable(props);
		return (
			<section aria-label="organization usage">
				{props.organizations?.map((organization) => (
					<p key={organization.organization_name}>
						{organization.organization_name}
					</p>
				)) ?? "No organization rows"}
			</section>
		);
	},
	KnowledgeStorageTable: (props: unknown) => {
		mockKnowledgeStorageTable(props);
		return (
			<section aria-label="knowledge storage">Knowledge storage</section>
		);
	},
}));

function makeUsageReport() {
	return {
		summary: {
			total_ai_cost: "12.34",
			total_input_tokens: 1000,
			total_output_tokens: 250,
			total_ai_calls: 3,
			total_cpu_seconds: 9.5,
			peak_memory_bytes: 1048576,
		},
		trends: [
			{
				date: "2026-09-09",
				ai_cost: "12.34",
				input_tokens: 1000,
				output_tokens: 250,
			},
		],
		by_workflow: [
			{
				workflow_name: "Invoice sync",
				execution_count: 2,
				input_tokens: 800,
				output_tokens: 120,
				ai_cost: "9.87",
				cpu_seconds: 8,
				memory_bytes: 1048576,
			},
		],
		by_conversation: [
			{
				conversation_id: "conversation-1",
				conversation_title: "Ticket triage chat",
				message_count: 4,
				input_tokens: 200,
				output_tokens: 130,
				ai_cost: "2.47",
			},
		],
		by_agent: [
			{
				agent_name: "Dispatcher",
				run_count: 1,
				input_tokens: 100,
				output_tokens: 40,
				ai_cost: "1.11",
			},
		],
		by_organization: [
			{
				organization_id: "org-1",
				organization_name: "Acme Corp",
				execution_count: 2,
				conversation_count: 1,
				input_tokens: 1000,
				output_tokens: 250,
				ai_cost: "12.34",
			},
		],
		knowledge_storage: [],
		knowledge_storage_trends: [],
		knowledge_storage_as_of: "2026-09-09",
	};
}

function renderPage() {
	return renderWithProviders(<UsageReports />);
}

beforeEach(() => {
	vi.clearAllMocks();
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseOrganizations.mockReturnValue({
		data: [{ id: "org-1", name: "Acme Corp" }],
	});
	mockUseUsageReport.mockReturnValue({
		data: makeUsageReport(),
		isLoading: false,
		error: null,
		refetch: vi.fn(),
		isFetching: false,
	});
	mockUseUsageBreakdown.mockReturnValue({
		data: makeUsageBreakdown(),
		isLoading: false,
		error: null,
		refetch: vi.fn(),
		isFetching: false,
	});
});

function makeUsageBreakdown() {
	return {
		overall: {
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
			legacy_call_count: 0,
		},
		coverage: {
			started_attempt_count: 5,
			unobserved_attempt_count: 1,
			missing_cost_call_count: 1,
			unassigned_operation_call_count: 0,
			legacy_coverage_unknown: false,
			legacy_call_count: 0,
		},
		by_purpose: {
			items: [],
			total_groups: 0,
			limit: 50,
			offset: 0,
			omitted_group_count: 0,
		},
		by_provider_model: {
			items: [],
			total_groups: 0,
			limit: 50,
			offset: 0,
			omitted_group_count: 0,
		},
		by_profile: {
			items: [],
			total_groups: 0,
			limit: 50,
			offset: 0,
			omitted_group_count: 0,
		},
		by_organization: {
			items: [],
			total_groups: 0,
			limit: 50,
			offset: 0,
			omitted_group_count: 0,
		},
		by_operation: {
			items: [],
			total_groups: 0,
			limit: 50,
			offset: 0,
			omitted_group_count: 0,
		},
	};
}

describe("UsageReports", () => {
	it("shows retry instead of empty report claims when the first read fails", async () => {
		const refetch = vi.fn();
		mockUseUsageReport.mockReturnValue({
			data: undefined,
			isLoading: false,
			error: new Error("usage unavailable"),
			refetch,
			isFetching: false,
		});

		const { user } = await renderPage();

		expect(
			screen.getByText(
				"Usage data could not be loaded. Try again to retrieve this report.",
			),
		).toBeVisible();
		expect(screen.queryByText("Total AI Cost")).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Retry report" }));

		expect(refetch).toHaveBeenCalledTimes(1);
	});

	it("retains cached report content and exposes retry while a refresh is failing", async () => {
		const refetch = vi.fn();
		mockUseUsageReport.mockReturnValue({
			data: makeUsageReport(),
			isLoading: false,
			error: new Error("refresh failed"),
			refetch,
			isFetching: false,
		});

		const { user } = await renderPage();

		expect(
			screen.getByText(
				"Usage data could not be refreshed. Showing the last loaded report.",
			),
		).toBeVisible();
		expect(screen.getByText("Total AI Cost")).toBeVisible();
		expect(screen.getByText("Invoice sync")).toBeVisible();
		expect(screen.getByText("Ticket triage chat")).toBeVisible();

		await user.click(screen.getByRole("button", { name: "Retry report" }));

		expect(refetch).toHaveBeenCalledTimes(1);
	});

	it("passes source and organization filters to the report query and visible tables", async () => {
		const user = userEvent.setup();
		const { rerender } = await renderPage();

		expect(mockUseUsageReport).toHaveBeenLastCalledWith(
			expect.any(String),
			expect.any(String),
			"all",
			undefined,
		);
		expect(
			screen.getByRole("region", { name: "workflow usage" }),
		).toBeVisible();
		expect(
			screen.getByRole("region", { name: "conversation usage" }),
		).toBeVisible();
		expect(
			screen.getByRole("region", { name: "agent usage" }),
		).toBeVisible();
		expect(
			screen.getByRole("region", { name: "organization usage" }),
		).toBeVisible();

		await user.click(screen.getByRole("tab", { name: "Chat" }));
		const { UsageReports } = await import("./UsageReports");
		rerender(<UsageReports />);

		expect(mockUseUsageReport).toHaveBeenLastCalledWith(
			expect.any(String),
			expect.any(String),
			"chat",
			undefined,
		);
		expect(
			screen.queryByRole("region", { name: "workflow usage" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("region", { name: "conversation usage" }),
		).toBeVisible();
		expect(
			screen.queryByRole("region", { name: "agent usage" }),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Choose Acme" }));
		rerender(<UsageReports />);

		expect(mockUseUsageReport).toHaveBeenLastCalledWith(
			expect.any(String),
			expect.any(String),
			"chat",
			"org-1",
		);
		expect(
			screen.queryByRole("region", { name: "organization usage" }),
		).not.toBeInTheDocument();
	});

	it("passes usage breakdown filters from report filters and user-selected quality filters", async () => {
		const user = userEvent.setup();
		const { rerender } = await renderPage();

		expect(mockUseUsageBreakdown).toHaveBeenLastCalledWith(
			expect.objectContaining({
				start_date: expect.any(String),
				end_date: expect.any(String),
				source: "all",
			}),
		);
		expect(mockUseUsageBreakdown.mock.calls.at(-1)?.[0]).not.toHaveProperty(
			"org_id",
		);

		const lastProps = mockTestingReviewBreakdown.mock.calls.at(-1)?.[0] as {
			onFiltersChange: (filters: {
				purpose?: string | null;
				provider?: string | null;
				model?: string | null;
				profile_id?: string | null;
				profile_fingerprint?: string | null;
			}) => void;
		};
		lastProps.onFiltersChange({
			purpose: "synthetic_judge",
			provider: "openai",
			model: "gpt-5-mini",
			profile_id: "profile-1",
			profile_fingerprint: "fingerprint-1",
		});
		rerender(<UsageReports />);

		expect(mockUseUsageBreakdown).toHaveBeenLastCalledWith(
			expect.objectContaining({
				source: "all",
				purpose: "synthetic_judge",
				provider: "openai",
				model: "gpt-5-mini",
				profile_id: "profile-1",
				profile_fingerprint: "fingerprint-1",
			}),
		);

		await user.click(screen.getByRole("tab", { name: "Agents" }));
		await user.click(screen.getByRole("button", { name: "Choose Acme" }));
		rerender(<UsageReports />);

		expect(mockUseUsageBreakdown).toHaveBeenLastCalledWith(
			expect.objectContaining({
				source: "agents",
				org_id: "org-1",
				purpose: "synthetic_judge",
			}),
		);
	});
});
