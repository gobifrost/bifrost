import { AgentSettingsTab } from "./AgentSettingsTab";
/**
 * Tests for AgentSettingsTab.
 *
 * Mocks useAuth + create/update mutations at module scope. Exercises the
 * create vs edit path, admin-only budget visibility, and form submission.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import {
	resolveInheritedMaxTokens,
} from "./AgentSettingsTab";

vi.mock("@/lib/api-client", async () => {
	const actual =
		await vi.importActual<typeof import("@/lib/api-client")>(
			"@/lib/api-client",
		);
	return {
		...actual,
		$api: {
			...actual.$api,
			useQuery: (_method: string, path: string) => {
				if (
					path === "/api/mcp-connections" ||
					path === "/api/mcp-servers"
				)
					return { data: [], isLoading: false };
				throw new Error(`Missing settings query fixture: ${path}`);
			},
		},
	};
});

const mockAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockAuth(),
}));

const mockCreateMutation = vi.fn();
const mockUpdateMutation = vi.fn();
vi.mock("@/hooks/useAgents", async () => {
	const actual =
		await vi.importActual<typeof import("@/hooks/useAgents")>(
			"@/hooks/useAgents",
		);
	return {
		...actual,
		useCreateAgent: () => ({
			mutateAsync: mockCreateMutation,
			isPending: false,
		}),
		useUpdateAgent: () => ({
			mutateAsync: mockUpdateMutation,
			isPending: false,
		}),
		useAgents: () => ({ data: [] }),
	};
});

const mockToolsGrouped = vi.fn();
vi.mock("@/hooks/useTools", () => ({
	useToolsGrouped: () => mockToolsGrouped(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [], isLoading: false }),
}));

const mockRolesQuery = vi.fn();
vi.mock("@/hooks/useRoles", () => ({
	useRoles: (options?: { enabled?: boolean }) => mockRolesQuery(options),
}));
vi.mock("@/hooks/useKnowledge", () => ({
	useKnowledgeNamespaces: () => ({ data: [] }),
}));

const mockListModelProfiles = vi.hoisted(() => vi.fn());
vi.mock("@/services/aiModels", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/aiModels")>(
			"@/services/aiModels",
		);
	return {
		...actual,
		listModelProfiles: mockListModelProfiles,
	};
});
vi.mock("@/components/ai/ModelProfileSelector", () => ({
	ModelProfileSelector: ({
		label,
		value,
		onValueChange,
	}: {
		label?: string;
		value?: string | null;
		onValueChange: (value: string) => void;
	}) => (
		<label>
			{label ?? "Model profile"}
			<select
				value={value ?? ""}
				onChange={(event) => onValueChange(event.target.value)}
			>
				<option value="">Use primary profile assignment</option>
				<option value="profile-support">Support profile</option>
			</select>
		</label>
	),
}));

beforeEach(() => {
	mockAuth.mockReturnValue({ isPlatformAdmin: false });
	mockCreateMutation.mockReset();
	mockUpdateMutation.mockReset();
	mockCreateMutation.mockResolvedValue({ id: "new-agent-id", name: "Bot" });
	mockUpdateMutation.mockResolvedValue({});
	mockToolsGrouped.mockReturnValue({
		data: { system: [], workflow: [] },
	});
	mockListModelProfiles.mockReset();
	mockListModelProfiles.mockResolvedValue([]);
	mockRolesQuery.mockReset();
	mockRolesQuery.mockReturnValue({
		data: [],
		isError: false,
		isLoading: false,
		isFetching: false,
		dataUpdatedAt: 0,
		refetch: vi.fn(),
	});
});

async function renderTab(
	props: Partial<{
		mode: "create" | "edit";
		agent: Record<string, unknown> | null;
		onCreated: (id: string) => void;
	}> = {},
) {

	return renderWithProviders(
		<AgentSettingsTab
			mode={props.mode ?? "edit"}
			// @ts-expect-error narrowed for tests
			agent={props.agent}
			onCreated={props.onCreated}
		/>,
	);
}

const existingAgent = {
	id: "agent-1",
	name: "Tier-1 Triage",
	description: "Triages support",
	system_prompt: "You are a triage bot.",
	channels: ["chat"],
	access_level: "role_based",
	is_active: true,
	tool_ids: [],
	delegated_agent_ids: [],
	role_ids: [],
	knowledge_sources: [],
	max_iterations: null,
	max_token_budget: null,
	llm_max_tokens: null,
	llm_profile_id: null,
};

describe("AgentSettingsTab — edit mode", () => {
	it("prepopulates fields from the agent", async () => {
		await renderTab({ mode: "edit", agent: existingAgent });
		const nameInput = screen.getByRole("textbox", {
			name: /^name$/i,
		}) as HTMLInputElement;
		expect(nameInput.value).toBe("Tier-1 Triage");
		const promptInput = screen.getByRole("textbox", {
			name: /system prompt/i,
		}) as HTMLTextAreaElement;
		expect(promptInput).toHaveTextContent("You are a triage bot.");
	});

	it("submits via update mutation on Save", async () => {
		const { user } = await renderTab({
			mode: "edit",
			agent: existingAgent,
		});
		await user.click(screen.getByRole("button", { name: /save changes/i }));
		await waitFor(() => {
			expect(mockUpdateMutation).toHaveBeenCalledTimes(1);
		});
		const args = mockUpdateMutation.mock.calls[0][0];
		expect(args.params.path.agent_id).toBe("agent-1");
		expect(args.body.name).toBe("Tier-1 Triage");
		expect(args.body.llm_profile_id).toBeNull();
		expect(mockCreateMutation).not.toHaveBeenCalled();
	});

	it("submits the selected reusable model profile for platform admins", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		const { user } = await renderTab({
			mode: "edit",
			agent: existingAgent,
		});
		await user.selectOptions(
			screen.getByLabelText(/model profile/i),
			"profile-support",
		);
		await user.click(screen.getByRole("button", { name: /save changes/i }));
		await waitFor(() => {
			expect(mockUpdateMutation).toHaveBeenCalledTimes(1);
		});
		expect(mockUpdateMutation.mock.calls[0][0].body.llm_profile_id).toBe(
			"profile-support",
		);
	});

	it("preserves the assigned profile without admin controls for organization users", async () => {
		const { user } = await renderTab({
			mode: "edit",
			agent: { ...existingAgent, llm_profile_id: "profile-support" },
		});
		expect(
			screen.queryByLabelText(/model profile/i),
		).not.toBeInTheDocument();
		expect(screen.getByText("Assigned model profile")).toBeVisible();
		await user.click(screen.getByRole("button", { name: /save changes/i }));
		await waitFor(() =>
			expect(mockUpdateMutation).toHaveBeenCalledTimes(1),
		);
		expect(mockUpdateMutation.mock.calls[0][0].body.llm_profile_id).toBe(
			"profile-support",
		);
	});

	it("hides the Budgets section for non-admin users", async () => {
		await renderTab({ mode: "edit", agent: existingAgent });
		expect(screen.queryByTestId("budget-card")).not.toBeInTheDocument();
	});

	it("shows the Budgets section for platform admins", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		await renderTab({ mode: "edit", agent: existingAgent });
		expect(screen.getByTestId("budget-card")).toBeInTheDocument();
		expect(screen.getAllByPlaceholderText("No limit")).toHaveLength(2);
		expect(
			screen.getByText("Optional LLM request limit (1–200)."),
		).toBeInTheDocument();
		expect(
			screen.getByText("Optional cumulative limit (1k–1M tokens)."),
		).toBeInTheDocument();
	});

	it("shows the inherit helper for max tokens per response", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		await renderTab({ mode: "edit", agent: existingAgent });
		expect(
			screen.getByText("Blank = inherit. Agent value wins when set."),
		).toBeInTheDocument();
	});
});

describe("AgentSettingsTab — inherited max tokens", () => {
	const profileWithDefault = {
		id: "profile-support",
		name: "Support profile",
		connection_id: "connection-1",
		model: "gpt-5-mini",
		capabilities: null,
		enabled_for_chat: true,
		default_max_tokens: 8000,
		connection: {
			id: "connection-1",
			name: "Default",
			provider: "openai",
			endpoint: null,
		},
		assignment_keys: [],
		referenced_agent_count: 0,
		created_at: "2026-08-22T00:00:00Z",
		updated_at: "2026-08-22T00:00:00Z",
	};

	it("shows the Inherit placeholder when no profile default exists", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockListModelProfiles.mockResolvedValue([]);
		await renderTab({ mode: "edit", agent: existingAgent });
		const input = await screen.findByLabelText(/max tokens \/ response/i);
		expect(input).toHaveAttribute("placeholder", "Inherit");
		expect(
			screen.queryByTestId("profile-default-max-tokens"),
		).not.toBeInTheDocument();
	});

	it("shows the profile placeholder and profile default when selected", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockListModelProfiles.mockResolvedValue([profileWithDefault]);
		const { user } = await renderTab({
			mode: "edit",
			agent: existingAgent,
		});
		await user.selectOptions(
			screen.getByLabelText(/model profile/i),
			"profile-support",
		);
		const input = await screen.findByPlaceholderText("8,000 (profile)");
		expect(input).toHaveAttribute("placeholder", "8,000 (profile)");
		expect(
			screen.getByTestId("profile-default-max-tokens"),
		).toHaveTextContent("Profile default: 8,000");
	});

	it("sends null when the agent field is cleared back to inherit", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockListModelProfiles.mockResolvedValue([profileWithDefault]);
		const { user } = await renderTab({
			mode: "edit",
			agent: {
				...existingAgent,
				llm_profile_id: "profile-support",
				llm_max_tokens: 4000,
			},
		});
		const input = (await screen.findByLabelText(
			/max tokens \/ response/i,
		)) as HTMLInputElement;
		expect(input.value).toBe("4000");
		await user.clear(input);
		expect(input.value).toBe("");
		await user.click(screen.getByRole("button", { name: /save changes/i }));
		await waitFor(() => {
			expect(mockUpdateMutation).toHaveBeenCalledTimes(1);
		});
		expect(
			mockUpdateMutation.mock.calls[0][0].body.llm_max_tokens,
		).toBeNull();
	});

	it("sends the explicit agent value when set", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockListModelProfiles.mockResolvedValue([]);
		const { user } = await renderTab({
			mode: "edit",
			agent: existingAgent,
		});
		const input = await screen.findByLabelText(/max tokens \/ response/i);
		await user.type(input, "4000");
		await user.click(screen.getByRole("button", { name: /save changes/i }));
		await waitFor(() => {
			expect(mockUpdateMutation).toHaveBeenCalledTimes(1);
		});
		expect(
			mockUpdateMutation.mock.calls[0][0].body.llm_max_tokens,
		).toBe(4000);
	});

	it("resolves precedence profile > provider default, no generic default", () => {
		const anthropicProfile = {
			id: "profile-anthropic",
			name: "Anthropic profile",
			connection_id: "connection-2",
			model: "claude-sonnet-4-5",
			connection: {
				id: "connection-2",
				name: "Anthropic",
				provider: "anthropic",
				endpoint: null,
			},
		};
		// Agent value wins is handled by the form (explicit input); the
		// resolver covers the inherit chain below it.
		expect(
			resolveInheritedMaxTokens({
				...anthropicProfile,
				default_max_tokens: 8000,
			} as never),
		).toEqual({ value: 8000, source: "profile" });
		// No generic harness default: empty means inherit (provider default).
		expect(resolveInheritedMaxTokens(null)).toBeNull();
		expect(
			resolveInheritedMaxTokens(anthropicProfile as never, {
				harnessDefault: null,
			}),
		).toEqual({ value: 16384, source: "Anthropic" });
		expect(
			resolveInheritedMaxTokens(null, { harnessDefault: null }),
		).toBeNull();
		// Explicit harnessDefault opt still honored for backward compat.
		expect(
			resolveInheritedMaxTokens(null, { harnessDefault: 8000 }),
		).toEqual({ value: 8000, source: "default" });
	});
});

describe("AgentSettingsTab — create mode", () => {
	it("renders an empty form with Create label on the submit button", async () => {
		await renderTab({ mode: "create", agent: null });
		expect(
			screen.getByRole("button", { name: /create agent/i }),
		).toBeInTheDocument();
	});

	it("blocks submission when name + system prompt are empty", async () => {
		const { user } = await renderTab({ mode: "create", agent: null });
		await user.click(screen.getByRole("button", { name: /create agent/i }));
		// Validation prevents the create mutation from firing.
		await waitFor(() => {
			expect(screen.getAllByText(/required/i).length).toBeGreaterThan(0);
		});
		expect(mockCreateMutation).not.toHaveBeenCalled();
	});

	it("calls create mutation and onCreated with the new agent id", async () => {
		const onCreated = vi.fn();
		const { user } = await renderTab({
			mode: "create",
			agent: null,
			onCreated,
		});
		await user.type(
			screen.getByRole("textbox", { name: /^name$/i }),
			"Sales Bot",
		);
		await user.type(
			screen.getByRole("textbox", { name: /system prompt/i }),
			"Be helpful.",
		);
		await user.click(screen.getByRole("button", { name: /create agent/i }));
		await waitFor(() => {
			expect(mockCreateMutation).toHaveBeenCalledTimes(1);
		});
		expect(mockCreateMutation.mock.calls[0][0].body.name).toBe("Sales Bot");
		expect(mockCreateMutation.mock.calls[0][0].body.access_level).toBe(
			"private",
		);
		expect(
			mockCreateMutation.mock.calls[0][0].body.llm_profile_id,
		).toBeNull();
		expect(onCreated).toHaveBeenCalledWith("new-agent-id");
	});
});

describe("AgentSettingsTab — tool audience validation", () => {
	const ORG_A = "org-aaaa-1111-1111-1111-aaaaaaaaaaaa";
	const ORG_B = "org-bbbb-2222-2222-2222-bbbbbbbbbbbb";
	const systemTool = {
		id: "system.search",
		name: "search",
		type: "system",
		description: "System search",
		is_active: true,
		organization_id: null,
		organization_name: null,
	};
	const globalTool = {
		id: "wf-global",
		name: "global_wf",
		type: "workflow",
		description: "Global workflow",
		is_active: true,
		organization_id: null,
		organization_name: null,
	};
	const orgATool = {
		id: "wf-org-a",
		name: "org_a_wf",
		type: "workflow",
		description: "Org A workflow",
		is_active: true,
		organization_id: ORG_A,
		organization_name: "Acme",
	};
	const orgBTool = {
		id: "wf-org-b",
		name: "org_b_wf",
		type: "workflow",
		description: "Org B workflow",
		is_active: true,
		organization_id: ORG_B,
		organization_name: "Beta",
	};

	it("shows the error banner and disables Save when a tool belongs to a different org", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockToolsGrouped.mockReturnValue({
			data: { system: [systemTool], workflow: [orgATool, orgBTool] },
		});
		await renderTab({
			mode: "edit",
			agent: {
				...existingAgent,
				organization_id: ORG_A,
				tool_ids: ["wf-org-b"],
			},
		});
		expect(
			await screen.findByTestId("tool-mismatch-banner"),
		).toBeInTheDocument();
		expect(screen.getByTestId("save-agent-button")).toBeDisabled();
	});

	it("allows Save and hides banners when attached tool is global", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockToolsGrouped.mockReturnValue({
			data: { system: [systemTool], workflow: [globalTool] },
		});
		await renderTab({
			mode: "edit",
			agent: {
				...existingAgent,
				organization_id: ORG_A,
				tool_ids: ["wf-global"],
			},
		});
		expect(
			screen.queryByTestId("tool-mismatch-banner"),
		).not.toBeInTheDocument();
		expect(
			screen.queryByTestId("tool-global-info-banner"),
		).not.toBeInTheDocument();
		expect(screen.getByTestId("save-agent-button")).not.toBeDisabled();
	});

	it("shows the info banner on a global agent with org-scoped tools attached", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockToolsGrouped.mockReturnValue({
			data: { system: [systemTool], workflow: [orgATool] },
		});
		await renderTab({
			mode: "edit",
			agent: {
				...existingAgent,
				organization_id: null, // global agent
				tool_ids: ["wf-org-a"],
			},
		});
		expect(
			await screen.findByTestId("tool-global-info-banner"),
		).toBeInTheDocument();
		expect(
			screen.queryByTestId("tool-mismatch-banner"),
		).not.toBeInTheDocument();
		expect(screen.getByTestId("save-agent-button")).not.toBeDisabled();
	});

	it("does not flag system tools regardless of agent org", async () => {
		mockAuth.mockReturnValue({ isPlatformAdmin: true });
		mockToolsGrouped.mockReturnValue({
			data: { system: [systemTool], workflow: [] },
		});
		await renderTab({
			mode: "edit",
			agent: {
				...existingAgent,
				organization_id: ORG_A,
				system_tools: ["system.search"],
			},
		});
		expect(
			screen.queryByTestId("tool-mismatch-banner"),
		).not.toBeInTheDocument();
		expect(screen.getByTestId("save-agent-button")).not.toBeDisabled();
	});
});

it("retains edited values after a failed save and retries the same draft", async () => {
	mockUpdateMutation
		.mockRejectedValueOnce(new Error("offline"))
		.mockResolvedValueOnce(existingAgent);
	const { user } = await renderTab({ mode: "edit", agent: existingAgent });
	const name = screen.getByLabelText("Name");
	await user.clear(name);
	await user.type(name, "Updated triage");
	await user.click(screen.getByTestId("save-agent-button"));
	expect(await screen.findByRole("alert")).toHaveTextContent(
		"Your changes are still here",
	);
	expect(name).toHaveValue("Updated triage");
	expect(screen.getByRole("alert")).toHaveFocus();
	await user.click(screen.getByRole("button", { name: "Retry save" }));
	await waitFor(() => expect(mockUpdateMutation).toHaveBeenCalledTimes(2));
	expect(mockUpdateMutation.mock.calls[1][0].body.name).toBe(
		"Updated triage",
	);
});

it("does not request or alert on roles for a private member agent", async () => {
	mockRolesQuery.mockReturnValue({
		data: undefined,
		isError: true,
		isLoading: false,
		isFetching: false,
		dataUpdatedAt: 0,
		refetch: vi.fn(),
	});

	await renderTab({
		mode: "edit",
		agent: {
			...existingAgent,
			access_level: "private",
			role_ids: [],
		},
	});

	expect(mockRolesQuery).toHaveBeenCalledWith({ enabled: false });
	expect(screen.queryByText("Assigned roles")).not.toBeInTheDocument();
	expect(
		screen.queryByText(/Could not load available roles/i),
	).not.toBeInTheDocument();
});

it("still exposes role loading failures when an admin edits role-based access", async () => {
	mockAuth.mockReturnValue({
		isPlatformAdmin: true,
		user: { organizationId: "org-1" },
	});
	mockRolesQuery.mockReturnValue({
		data: undefined,
		isError: true,
		isLoading: false,
		isFetching: false,
		dataUpdatedAt: 0,
		refetch: vi.fn(),
	});
	await renderTab({ mode: "edit", agent: existingAgent });
	expect(mockRolesQuery).toHaveBeenCalledWith({ enabled: true });
	expect(screen.getByText(/Could not load available roles/i)).toBeVisible();
	expect(
		screen.getByRole("button", { name: "Retry available roles" }),
	).toBeEnabled();
});

it("renders solution-managed settings as read-only", async () => {
	await renderTab({
		mode: "edit",
		agent: { ...existingAgent, is_solution_managed: true },
	});
	expect(screen.getByLabelText("Name")).toBeDisabled();
	expect(screen.getByLabelText("System prompt")).toHaveAttribute(
		"contenteditable",
		"false",
	);
	expect(screen.getByRole("combobox", { name: "Tools" })).toBeDisabled();
	expect(screen.getByTestId("save-agent-button")).toBeDisabled();
	expect(screen.getByTestId("solution-managed-banner")).toBeVisible();
});

it("uses private scope for regular users and hides sharing controls", async () => {
	await renderTab({ mode: "create", agent: null });
	expect(screen.getByRole("combobox", { name: "Scope" })).toHaveTextContent(
		"Only me",
	);
	expect(
		screen.queryByRole("combobox", { name: "Access level" }),
	).not.toBeInTheDocument();
	expect(screen.queryByText("Assigned roles")).not.toBeInTheDocument();
});
it("lets admins select private scope and restore sharing controls", async () => {
	mockAuth.mockReturnValue({
		isPlatformAdmin: true,
		user: { organizationId: "org-1" },
	});
	const { user } = await renderTab({ mode: "create", agent: null });
	await user.click(screen.getByRole("combobox", { name: "Scope" }));
	await user.click(screen.getByRole("option", { name: /Only me/ }));
	expect(
		screen.queryByRole("combobox", { name: "Access level" }),
	).not.toBeInTheDocument();
	expect(screen.queryByText("Assigned roles")).not.toBeInTheDocument();
	await user.click(screen.getByRole("combobox", { name: "Scope" }));
	await user.click(screen.getByRole("option", { name: /Global/ }));
	expect(
		screen.getByRole("combobox", { name: "Access level" }),
	).toBeInTheDocument();
});
