import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

const aiModels = vi.hoisted(() => ({
	listProviderConnections: vi.fn(),
	listModelProfiles: vi.fn(),
	listModelAssignments: vi.fn(),
	listProviderModels: vi.fn(),
	createProviderConnection: vi.fn(),
	updateProviderConnection: vi.fn(),
	createModelProfile: vi.fn(),
	updateModelProfile: vi.fn(),
	setModelAssignment: vi.fn(),
	deleteProviderConnection: vi.fn(),
	deleteModelProfile: vi.fn(),
	mergeModelProfiles: vi.fn(),
	testProviderConnection: vi.fn(),
	verifyProviderConnection: vi.fn(),
}));

vi.mock("@/services/aiModels", async () => {
	const actual = await vi.importActual<typeof import("@/services/aiModels")>(
		"@/services/aiModels",
	);
	return {
		...actual,
		...aiModels,
	};
});

vi.mock("@/components/ai/ModelProfileSelector", () => ({
	ModelProfileSelector: ({
		label,
		value,
		onValueChange,
		chatOnly,
		isSaving,
	}: {
		label: string;
		value?: string | null;
		onValueChange: (value: string) => void;
		chatOnly?: boolean;
		isSaving?: boolean;
	}) => (
		<button
			type="button"
			disabled={isSaving}
			onClick={() =>
				onValueChange(chatOnly ? "profile-chat" : "profile-1")
			}
		>
			{label}
			{value ? `: ${value}` : ""}
			{isSaving ? " · Saving assignment…" : ""}
		</button>
	),
}));

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

import { AIModelSettings } from "./AIModelSettings";

const provider = {
	id: "provider-1",
	name: "Default",
	provider: "openai" as const,
	endpoint: null,
	api_key_set: true,
	profile_count: 1,
	created_at: "2026-08-22T00:00:00Z",
	updated_at: "2026-08-22T00:00:00Z",
};

const profile = {
	id: "profile-1",
	name: "Balanced",
	connection_id: provider.id,
	model: "gpt-5-mini",
	capabilities: null,
	enabled_for_chat: true,
	connection: {
		id: provider.id,
		name: provider.name,
		provider: provider.provider,
		endpoint: provider.endpoint,
	},
	assignment_keys: ["primary" as const],
	referenced_agent_count: 0,
	created_at: "2026-08-22T00:00:00Z",
	updated_at: "2026-08-22T00:00:00Z",
};

describe("AIModelSettings", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		aiModels.listProviderConnections.mockResolvedValue([provider]);
		aiModels.listModelProfiles.mockResolvedValue([profile]);
		aiModels.listModelAssignments.mockResolvedValue([
			{
				assignment_key: "chat_default",
				profile_id: "profile-1",
				profile,
				created_at: "2026-08-22T00:00:00Z",
				updated_at: "2026-08-22T00:00:00Z",
			},
		]);
		aiModels.listProviderModels.mockResolvedValue({
			provider: "openai",
			models: [
				{
					id: "gpt-5",
					display_name: "GPT-5",
					output_modalities: ["text"],
				},
				{
					id: "gpt-5.1-mini",
					display_name: "GPT-5.1 mini",
					output_modalities: ["text"],
				},
			],
		});
		aiModels.verifyProviderConnection.mockResolvedValue({
			success: true,
			message: "Connected",
			models: [],
		});
		aiModels.createProviderConnection.mockResolvedValue(provider);
		aiModels.updateProviderConnection.mockResolvedValue(provider);
		aiModels.createModelProfile.mockResolvedValue(profile);
		aiModels.updateModelProfile.mockResolvedValue(profile);
		aiModels.setModelAssignment.mockResolvedValue({
			assignment_key: "primary",
			profile_id: "profile-1",
			profile,
			created_at: "2026-08-22T00:00:00Z",
			updated_at: "2026-08-22T00:00:00Z",
		});
		aiModels.deleteProviderConnection.mockResolvedValue(undefined);
		aiModels.deleteModelProfile.mockResolvedValue(undefined);
		aiModels.mergeModelProfiles.mockResolvedValue({
			profile,
			merged_profile_ids: ["profile-disabled"],
			reassigned_agent_count: 2,
			reassigned_assignment_keys: ["summarization"],
		});
		aiModels.testProviderConnection.mockResolvedValue({
			success: true,
			message: "Connection ok",
			models: [],
		});
	});

	it("shows providers, profiles, chat status, and assignments", async () => {
		renderWithProviders(<AIModelSettings />);

		expect(
			await screen.findByRole("heading", { name: "Models" }),
		).toBeInTheDocument();
		expect(
			await screen.findByRole("button", { name: "Test Default" }),
		).toBeInTheDocument();
		expect(await screen.findByText("Balanced")).toBeInTheDocument();
		expect(screen.getAllByText("Chat")[0]).toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: "Chat Profile: profile-1",
			}),
		).toBeInTheDocument();
		expect(
			screen.getAllByText("Profiles required for assignments")[0],
		).toBeInTheDocument();
	});

	it("creates provider connections and model profiles", async () => {
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(
			await screen.findByRole("button", { name: "Add Provider" }),
		);
		let dialog = screen.getByRole("dialog");
		await user.click(
			within(dialog).getByRole("combobox", { name: "Provider" }),
		);
		await user.click(screen.getByRole("option", { name: "OpenRouter" }));
		expect(within(dialog).getByLabelText("Endpoint")).toHaveValue(
			"https://openrouter.ai/api/v1",
		);
		await user.type(
			within(dialog).getByLabelText("API Key"),
			"sk-provider",
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Add Provider" }),
		);

		await waitFor(() =>
			expect(aiModels.verifyProviderConnection).toHaveBeenCalled(),
		);
		await waitFor(() =>
			expect(aiModels.createProviderConnection).toHaveBeenCalled(),
		);
		expect(aiModels.createProviderConnection.mock.calls[0][0]).toEqual(
			expect.objectContaining({
				name: "OpenRouter",
				provider: "openrouter",
				api_key: "sk-provider",
				endpoint: "https://openrouter.ai/api/v1",
			}),
		);

		await user.click(screen.getByRole("button", { name: "Add Profile" }));
		dialog = screen.getByRole("dialog");
		expect(
			within(dialog).queryByLabelText("Max Tokens"),
		).not.toBeInTheDocument();
		await user.type(
			within(dialog).getByRole("textbox", { name: "Profile Name" }),
			"Fast",
		);
		await user.click(
			within(dialog).getByRole("combobox", { name: "Model" }),
		);
		await user.click(screen.getByRole("option", { name: "GPT-5 gpt-5" }));
		await user.click(
			within(dialog).getByRole("button", { name: "Add Profile" }),
		);

		await waitFor(() =>
			expect(aiModels.createModelProfile).toHaveBeenCalled(),
		);
		expect(aiModels.createModelProfile.mock.calls[0][0]).toEqual(
			expect.objectContaining({
				name: "Fast",
				connection_id: "provider-1",
				model: "gpt-5",
			}),
		);
		expect(aiModels.createModelProfile.mock.calls[0][0]).not.toHaveProperty(
			"max_tokens",
		);
	});

	it("keeps an unverified provider unsaved and the dialog open", async () => {
		let finishVerification: (result: {
			success: boolean;
			message: string;
			models: never[];
		}) => void = () => undefined;
		aiModels.verifyProviderConnection.mockReturnValue(
			new Promise((resolve) => {
				finishVerification = resolve;
			}),
		);
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(
			await screen.findByRole("button", { name: "Add Provider" }),
		);
		const dialog = screen.getByRole("dialog");
		await user.type(within(dialog).getByLabelText("API Key"), "bad-key");
		await user.click(
			within(dialog).getByRole("button", { name: "Add Provider" }),
		);

		expect(
			within(dialog).getByRole("button", { name: "Verifying..." }),
		).toBeDisabled();
		expect(aiModels.createProviderConnection).not.toHaveBeenCalled();

		finishVerification({
			success: false,
			message: "Authentication failed",
			models: [],
		});
		await waitFor(() =>
			expect(
				within(dialog).getByRole("button", { name: "Add Provider" }),
			).toBeEnabled(),
		);
		expect(aiModels.createProviderConnection).not.toHaveBeenCalled();
		expect(screen.getByRole("dialog")).toBeInTheDocument();
	});

	it("uses actionable empty states to start provider and profile setup", async () => {
		aiModels.listProviderConnections.mockResolvedValue([]);
		aiModels.listModelProfiles.mockResolvedValue([]);
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(await screen.findByText("No providers"));
		expect(
			screen.getByRole("heading", { name: "Add Provider Connection" }),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Cancel" }));

		await user.click(screen.getByText("No model profiles"));
		expect(
			screen.getByRole("heading", { name: "Add Provider Connection" }),
		).toBeInTheDocument();
	});

	it("explains that the first profile becomes every assignment default", async () => {
		aiModels.listModelProfiles.mockResolvedValue([]);
		aiModels.listModelAssignments.mockResolvedValue([]);
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(
			await screen.findByRole("button", { name: "Add Profile" }),
		);
		const dialog = screen.getByRole("dialog");

		expect(
			within(dialog).getByText(
				"Your first profile starts as the default for every assignment.",
			),
		).toBeInTheDocument();
		expect(within(dialog).getByRole("switch")).toBeChecked();
		expect(within(dialog).getByRole("switch")).toBeDisabled();
	});

	it("assigns a selected reusable profile to a runtime slot", async () => {
		let finishAssignment: () => void = () => undefined;
		aiModels.setModelAssignment.mockReturnValue(
			new Promise((resolve) => {
				finishAssignment = () => {
					const assignment = {
						assignment_key: "primary",
						profile_id: "profile-1",
						profile,
						created_at: "2026-08-22T00:00:00Z",
						updated_at: "2026-08-22T00:00:00Z",
					} as const;
					aiModels.listModelAssignments.mockResolvedValue([
						{
							assignment_key: "chat_default",
							profile_id: "profile-1",
							profile,
							created_at: "2026-08-22T00:00:00Z",
							updated_at: "2026-08-22T00:00:00Z",
						},
						assignment,
					]);
					resolve(assignment);
				};
			}),
		);
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(
			await screen.findByRole("button", { name: "Default Profile" }),
		);

		await waitFor(() =>
			expect(aiModels.setModelAssignment).toHaveBeenCalledWith(
				"primary",
				"profile-1",
			),
		);
		expect(
			screen.getByRole("button", {
				name: "Default Profile: profile-1 · Saving assignment…",
			}),
		).toBeDisabled();

		finishAssignment();
		await waitFor(() =>
			expect(
				screen.getByRole("button", {
					name: "Default Profile: profile-1",
				}),
			).toBeEnabled(),
		);
	});

	it("allows a non-Chat profile to become the platform default", async () => {
		const disabledProfile = {
			...profile,
			id: "profile-disabled",
			name: "Agent Only",
			enabled_for_chat: false,
			assignment_keys: [],
		};
		aiModels.listModelProfiles.mockResolvedValue([
			profile,
			disabledProfile,
		]);
		aiModels.setModelAssignment.mockResolvedValue({
			assignment_key: "primary",
			profile_id: disabledProfile.id,
			profile: disabledProfile,
			created_at: "2026-08-22T00:00:00Z",
			updated_at: "2026-08-22T00:00:00Z",
		});
		const { user } = renderWithProviders(<AIModelSettings />);

		const card = (await screen.findByText("Agent Only")).closest(
			'[data-slot="card"]',
		);
		expect(card).not.toBeNull();
		const button = within(card as HTMLElement).getByRole("button", {
			name: "Set Default",
		});
		expect(button).toBeEnabled();
		await user.click(button);

		await waitFor(() =>
			expect(aiModels.setModelAssignment).toHaveBeenCalledWith(
				"primary",
				"profile-disabled",
			),
		);
		expect(
			within(card as HTMLElement).getByRole("switch"),
		).not.toBeChecked();
	});

	it("merges selected profiles into a chosen reusable target", async () => {
		const agentProfile = {
			...profile,
			id: "profile-disabled",
			name: "Agent Only",
			model: "gpt-5",
			enabled_for_chat: false,
			assignment_keys: ["summarization" as const],
			referenced_agent_count: 2,
		};
		aiModels.listModelProfiles.mockResolvedValue([profile, agentProfile]);
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(
			await screen.findByRole("button", { name: "Merge Profiles" }),
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Select Balanced" }),
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Select Agent Only" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Merge Profiles" }),
		);

		const dialog = screen.getByRole("dialog");
		expect(
			within(dialog).getByRole("heading", {
				name: "Merge Model Profiles",
			}),
		).toBeInTheDocument();
		expect(
			within(dialog).getByText(
				"2 agents and 1 assignment will move to it.",
			),
		).toBeInTheDocument();
		await user.click(
			within(dialog).getByRole("radio", { name: /Agent Only/ }),
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Merge Profiles" }),
		);

		await waitFor(() =>
			expect(aiModels.mergeModelProfiles).toHaveBeenCalledWith({
				profile_ids: ["profile-1", "profile-disabled"],
				target_profile_id: "profile-disabled",
			}),
		);
	});

	it("edits provider connections and reusable profiles in place", async () => {
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(
			await screen.findByRole("button", { name: "Edit Default" }),
		);
		let dialog = screen.getByRole("dialog");
		const providerName = within(dialog).getByRole("textbox", {
			name: "Name",
		});
		await user.clear(providerName);
		await user.type(providerName, "OpenAI Production");
		await user.type(
			within(dialog).getByLabelText("New API Key"),
			"sk-rotated",
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Save Provider" }),
		);

		await waitFor(() =>
			expect(aiModels.updateProviderConnection).toHaveBeenCalledWith(
				"provider-1",
				expect.objectContaining({
					name: "OpenAI Production",
					api_key: "sk-rotated",
				}),
			),
		);

		await user.click(screen.getByRole("button", { name: "Edit Balanced" }));
		dialog = screen.getByRole("dialog");
		await user.click(
			within(dialog).getByRole("combobox", { name: "Model" }),
		);
		await user.click(
			screen.getByRole("option", {
				name: "GPT-5.1 mini gpt-5.1-mini",
			}),
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Save Profile" }),
		);

		await waitFor(() =>
			expect(aiModels.updateModelProfile).toHaveBeenCalledWith(
				"profile-1",
				expect.objectContaining({ model: "gpt-5.1-mini" }),
			),
		);
	});
});
