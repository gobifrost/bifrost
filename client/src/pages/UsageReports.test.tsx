import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen } from "@/test-utils";
import { UsageReports } from "./UsageReports";
import { useLocation } from "react-router-dom";

function LocationProbe() {
	const location = useLocation();
	return (
		<output role="status" aria-label="Current URL">
			{location.pathname}
			{location.search}
		</output>
	);
}

const mockUseAuth = vi.fn();
const mockUseOrganizations = vi.fn();
const mockUseUsageReport = vi.fn();
const mockUsageCharts = vi.fn();
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
}));

vi.mock("./WorkflowResourcesReport", () => ({
	WorkflowResourcesReport: ({
		reportSwitch,
	}: {
		reportSwitch: ReactNode;
	}) => (
		<section aria-label="workflow resources report">
			{reportSwitch}
			Resource runs
		</section>
	),
}));

vi.mock("@/components/layout/ListPageHeader", () => ({
	ListPageHeader: ({
		title,
		titleAccessory,
		description,
		actions,
	}: {
		title: string;
		titleAccessory?: ReactNode;
		description?: string;
		actions?: ReactNode;
	}) => (
		<header>
			<h1>{title}</h1>
			{titleAccessory}
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
	DateRangePicker: ({
		onDateRangeChange,
	}: {
		onDateRangeChange: (value: { from: Date; to: Date }) => void;
	}) => (
		<button
			type="button"
			onClick={() =>
				onDateRangeChange({
					from: new Date(2026, 8, 2),
					to: new Date(2026, 8, 13),
				})
			}
		>
			Choose report dates
		</button>
	),
}));

vi.mock("@/components/reports/UsageCharts", () => ({
	UsageCharts: (props: unknown) => {
		mockUsageCharts(props);
		return <section aria-label="usage chart">Usage chart</section>;
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
});

describe("UsageReports", () => {
	it("restores the selected report and AI filters from the URL", async () => {
		const { user } = renderWithProviders(
			<>
				<UsageReports />
				<LocationProbe />
			</>,
			{
				initialEntries: [
					"/usage?tab=workflow&ai_source=chat&ai_org=org-1&ai_from=2026-09-01&ai_to=2026-09-12",
				],
			},
		);
		expect(
			screen.getByRole("region", { name: "workflow resources report" }),
		).toBeVisible();
		await user.click(screen.getByRole("radio", { name: "AI Usage" }));
		expect(mockUseUsageReport).toHaveBeenLastCalledWith(
			"2026-09-01",
			"2026-09-12",
			"chat",
			"org-1",
		);
		expect(
			screen.getByRole("status", { name: "Current URL" }),
		).toHaveTextContent("ai_source=chat");
		expect(
			screen.getByRole("status", { name: "Current URL" }),
		).toHaveTextContent("tab=ai");
		await user.click(
			screen.getByRole("button", { name: "Choose report dates" }),
		);
		expect(
			screen.getByRole("status", { name: "Current URL" }),
		).toHaveTextContent("ai_to=2026-09-13");
	});

	it("keeps AI Usage as the default and switches to Workflow Resources beside the title", async () => {
		const { user } = await renderPage();
		expect(screen.getByRole("heading", { name: "Usage" })).toBeVisible();
		expect(screen.getByText("Total AI Cost")).toBeVisible();
		await user.click(
			screen.getByRole("radio", { name: "Workflow Resources" }),
		);
		expect(
			screen.getByRole("region", { name: "workflow resources report" }),
		).toBeVisible();
		expect(screen.queryByText("Total AI Cost")).not.toBeInTheDocument();
		await user.click(screen.getByRole("radio", { name: "AI Usage" }));
		expect(screen.getByText("Total AI Cost")).toBeVisible();
	});

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
});
