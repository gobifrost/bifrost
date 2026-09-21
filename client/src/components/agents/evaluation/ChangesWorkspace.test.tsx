import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ChangesWorkspace } from "./ChangesWorkspace";
import { agentPlatform } from "@/services/agentPlatform";

vi.mock("@/hooks/useAgents", () => ({
	useAgent: () => ({
		data: {
			id: "agent",
			name: "Support",
			system_prompt: "Live prompt",
			organization_id: null,
			updated_at: "2026-09-20T00:00:00Z",
		},
		isPending: false,
		refetch: vi.fn(),
	}),
	useAgents: () => ({ data: [], isPending: false }),
}));
vi.mock("@/hooks/useTools", () => ({
	useToolsGrouped: () => ({
		data: { workflow: [], system: [] },
		isPending: false,
	}),
	useSystemTools: () => ({ data: { tools: [] }, isPending: false }),
}));
vi.mock("@/services/aiModels", () => ({
	listModelProfiles: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/hooks/useAgentPlatformUpdates", () => ({
	useAgentPlatformUpdates: vi.fn(),
}));
vi.mock("@/components/ai/ModelProfileSelector", () => ({
	ModelProfileSelector: () => <div>Profile selector</div>,
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: {
		suite: vi.fn(),
		candidate: vi.fn(),
		execution: vi.fn(),
		results: vi.fn(),
		cases: vi.fn(),
		createCandidate: vi.fn(),
	},
}));

function baseProps(overrides = {}) {
	return {
		agentId: "agent",
		suiteId: "",
		candidateId: "",
		profileIds: [],
		matrixId: "",
		executionId: "",
		onNavigate: vi.fn(),
		...overrides,
	};
}

describe("ChangesWorkspace proposed changes", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});
	it("opens the create editor on demand and closes it again", async () => {
		vi.mocked(agentPlatform.suite).mockRejectedValue(new Error("no suite"));
		const { user } = renderWithProviders(
			<ChangesWorkspace {...baseProps()} />,
			{ initialEntries: ["/agents/agent/quality?tab=changes"] },
		);
		expect(
			await screen.findByRole("heading", { name: "Proposed changes" }),
		).toBeVisible();
		expect(
			screen.queryByLabelText("Candidate name"),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Create proposed changes" }),
		);
		expect(screen.getByLabelText("Candidate name")).toBeVisible();
		await user.click(
			screen.getByRole("button", { name: "Close editor" }),
		);
		expect(
			screen.queryByLabelText("Candidate name"),
		).not.toBeInTheDocument();
	});

	it("shows a compact selected strip without repeating the change", async () => {
		vi.mocked(agentPlatform.suite).mockResolvedValue({
			id: "suite-1",
			name: "Regression",
			status: "draft",
			version: 1,
		} as never);
		vi.mocked(agentPlatform.candidate).mockResolvedValue({
			id: "cand-1",
			name: "Fix routing",
			overlays: { system_prompt: "Ask first." },
			snapshot: {},
			snapshot_hash: "abc",
			evaluation_only: true,
		} as never);
		const onNavigate = vi.fn();
		const { user } = renderWithProviders(
			<ChangesWorkspace
				{...baseProps({
					suiteId: "suite-1",
					candidateId: "cand-1",
					onNavigate,
				})}
			/>,
			{
				initialEntries: [
					"/agents/agent/quality?tab=changes&suite=suite-1&candidate=cand-1",
				],
			},
		);
		expect(
			await screen.findByRole("heading", { name: "Proposed changes" }),
		).toBeVisible();
		expect(await screen.findByText("Fix routing")).toBeVisible();
		expect(screen.getByText("Prompt updated")).toBeVisible();
		expect(screen.queryByText(/Prompt changed/)).not.toBeInTheDocument();
		expect(screen.queryByText(/Prompt edited/)).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Use live agent only" }),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Create proposed changes" }),
		).toBeVisible();
		expect(
			screen.getByText("Snapshot internals", { selector: "summary" }),
		).toBeVisible();
		// The editor stays closed while a set is selected.
		expect(
			screen.queryByLabelText("Candidate name"),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Use live agent only" }),
		);
		expect(onNavigate).toHaveBeenCalledWith({
			candidate: undefined,
			execution: undefined,
		});
	});

	it("does not offer creation while a selected set is still loading", async () => {
		vi.mocked(agentPlatform.suite).mockResolvedValue({
			id: "suite-1",
			name: "Regression",
			status: "draft",
			version: 1,
		} as never);
		let resolveCandidate!: (value: unknown) => void;
		vi.mocked(agentPlatform.candidate).mockReturnValue(
			new Promise((resolve) => {
				resolveCandidate = resolve;
			}) as never,
		);
		renderWithProviders(
			<ChangesWorkspace
				{...baseProps({
					suiteId: "suite-1",
					candidateId: "cand-1",
				})}
			/>,
			{
				initialEntries: [
					"/agents/agent/quality?tab=changes&suite=suite-1&candidate=cand-1",
				],
			},
		);
		expect(
			await screen.findByText("Loading proposed changes…"),
		).toBeVisible();
		expect(
			screen.queryByRole("button", { name: "Create proposed changes" }),
		).not.toBeInTheDocument();
		resolveCandidate({
			id: "cand-1",
			name: "Fix routing",
			overlays: {},
			snapshot: {},
			snapshot_hash: "abc",
			evaluation_only: true,
		});
		expect(await screen.findByText("Fix routing")).toBeVisible();
	});
});
