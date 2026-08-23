import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

const aiModels = vi.hoisted(() => ({
	listProviderConnections: vi.fn(),
	listModelProfiles: vi.fn(),
	listModelAssignments: vi.fn(),
	createProviderConnection: vi.fn(),
	updateProviderConnection: vi.fn(),
	createModelProfile: vi.fn(),
	updateModelProfile: vi.fn(),
	setModelAssignment: vi.fn(),
	deleteProviderConnection: vi.fn(),
	deleteModelProfile: vi.fn(),
	testProviderConnection: vi.fn(),
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
		onValueChange,
		chatOnly,
	}: {
		label: string;
		onValueChange: (value: string) => void;
		chatOnly?: boolean;
	}) => (
		<button
			type="button"
			onClick={() => onValueChange(chatOnly ? "profile-chat" : "profile-1")}
		>
			{label}
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
	max_tokens: 16384,
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
		aiModels.testProviderConnection.mockResolvedValue({
			success: true,
			message: "Connection ok",
			models: [],
		});
	});

	it("shows providers, profiles, chat status, and assignments", async () => {
		renderWithProviders(<AIModelSettings />);

		expect(
			await screen.findByRole("heading", { name: "AI Model Settings" }),
		).toBeInTheDocument();
		expect(
			await screen.findByRole("button", { name: "Test Default" }),
		).toBeInTheDocument();
		expect(await screen.findByText("Balanced")).toBeInTheDocument();
		expect(screen.getAllByText("Chat")[0]).toBeInTheDocument();
		expect(screen.getByText("Default Chat Profile")).toBeInTheDocument();
		expect(
			screen.getAllByText("Profiles required for assignments")[0],
		).toBeInTheDocument();
	});

	it("creates provider connections and model profiles", async () => {
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.type(
			await screen.findByRole("textbox", { name: "Provider Name" }),
			"OpenRouter",
		);
		await user.type(screen.getByLabelText("API Key"), "sk-provider");
		await user.click(screen.getByRole("button", { name: "Add Provider" }));

		await waitFor(() =>
			expect(aiModels.createProviderConnection).toHaveBeenCalled(),
		);
		expect(aiModels.createProviderConnection.mock.calls[0][0]).toEqual(
			expect.objectContaining({
				name: "OpenRouter",
				provider: "openai",
				api_key: "sk-provider",
			}),
		);

		await user.type(
			screen.getByRole("textbox", { name: "Profile Name" }),
			"Fast",
		);
		await user.click(
			screen.getByRole("combobox", { name: "Provider Connection" }),
		);
		await user.click(screen.getByRole("option", { name: /Default/ }));
		await user.type(screen.getByRole("textbox", { name: "Model" }), "gpt-5");
		await user.click(screen.getByRole("button", { name: "Create Profile" }));

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
	});

	it("assigns a selected reusable profile to a runtime slot", async () => {
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(await screen.findByText("Primary Profile"));

		await waitFor(() =>
			expect(aiModels.setModelAssignment).toHaveBeenCalledWith(
				"primary",
				"profile-1",
			),
		);
	});

	it("edits provider connections and reusable profiles in place", async () => {
		const { user } = renderWithProviders(<AIModelSettings />);

		await user.click(await screen.findByRole("button", { name: "Edit Default" }));
		let dialog = screen.getByRole("dialog");
		const providerName = within(dialog).getByRole("textbox", { name: "Name" });
		await user.clear(providerName);
		await user.type(providerName, "OpenAI Production");
		await user.type(within(dialog).getByLabelText("New API Key"), "sk-rotated");
		await user.click(within(dialog).getByRole("button", { name: "Save Provider" }));

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
		const profileModel = within(dialog).getByRole("textbox", { name: "Model" });
		await user.clear(profileModel);
		await user.type(profileModel, "gpt-5.1-mini");
		await user.click(within(dialog).getByRole("button", { name: "Save Profile" }));

		await waitFor(() =>
			expect(aiModels.updateModelProfile).toHaveBeenCalledWith(
				"profile-1",
				expect.objectContaining({ model: "gpt-5.1-mini" }),
			),
		);
	});
});
