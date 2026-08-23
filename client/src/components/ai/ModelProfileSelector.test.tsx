import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

const aiModels = vi.hoisted(() => ({
	listModelProfiles: vi.fn(),
	listProviderConnections: vi.fn(),
	createModelProfile: vi.fn(),
}));

vi.mock("@/services/aiModels", async () => {
	const actual = await vi.importActual<typeof import("@/services/aiModels")>(
		"@/services/aiModels",
	);
	return {
		...actual,
		listModelProfiles: aiModels.listModelProfiles,
		listProviderConnections: aiModels.listProviderConnections,
		createModelProfile: aiModels.createModelProfile,
	};
});

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

import { ModelProfileSelector } from "./ModelProfileSelector";

const provider = {
	id: "provider-1",
	name: "OpenRouter",
	provider: "openrouter" as const,
	endpoint: "https://openrouter.ai/api/v1",
	api_key_set: true,
	profile_count: 1,
	created_at: "2026-08-22T00:00:00Z",
	updated_at: "2026-08-22T00:00:00Z",
};

const profile = {
	id: "profile-1",
	name: "Balanced Chat",
	connection_id: provider.id,
	model: "openai/gpt-5-mini",
	capabilities: null,
	enabled_for_chat: true,
	connection: {
		id: provider.id,
		name: provider.name,
		provider: provider.provider,
		endpoint: provider.endpoint,
	},
	assignment_keys: [],
	referenced_agent_count: 0,
	created_at: "2026-08-22T00:00:00Z",
	updated_at: "2026-08-22T00:00:00Z",
};

describe("ModelProfileSelector", () => {
	beforeEach(() => {
		aiModels.listModelProfiles.mockResolvedValue([profile]);
		aiModels.listProviderConnections.mockResolvedValue([provider]);
		aiModels.createModelProfile.mockResolvedValue({
			...profile,
			id: "profile-2",
			name: "New Chat",
		});
	});

	it("selects reusable profiles only", async () => {
		const onValueChange = vi.fn();
		const { user } = renderWithProviders(
			<ModelProfileSelector
				label="Runtime Profile"
				value={null}
				onValueChange={onValueChange}
			/>,
		);

		await user.click(await screen.findByRole("combobox"));
		await user.click(screen.getByRole("option", { name: /Balanced Chat/ }));

		expect(onValueChange).toHaveBeenCalledWith("profile-1");
		expect(
			screen.queryByText("openai/gpt-5-mini", { selector: "option" }),
		).not.toBeInTheDocument();
	});

	it("shows and locks the selector while an assignment is saving", async () => {
		renderWithProviders(
			<ModelProfileSelector
				value="profile-1"
				onValueChange={vi.fn()}
				isSaving
			/>,
		);

		expect(await screen.findByRole("status")).toHaveTextContent(
			"Saving assignment…",
		);
		expect(screen.getByRole("combobox")).toBeDisabled();
	});

	it("creates a profile from the inline dialog and selects it", async () => {
		const onValueChange = vi.fn();
		const { user } = renderWithProviders(
			<ModelProfileSelector
				label="Chat Profile"
				value={null}
				onValueChange={onValueChange}
				chatOnly
			/>,
		);

		await user.click(
			await screen.findByRole("button", { name: /create profile/i }),
		);
		expect(screen.queryByLabelText("Max Tokens")).not.toBeInTheDocument();
		await user.type(
			screen.getByRole("textbox", { name: "Profile Name" }),
			"New Chat",
		);
		await user.click(
			screen.getByRole("combobox", { name: "Provider Connection" }),
		);
		await user.click(screen.getByRole("option", { name: /OpenRouter/ }));
		await user.type(
			screen.getByRole("textbox", { name: "Model" }),
			"gpt-5",
		);
		await user.click(screen.getByRole("button", { name: "Create" }));

		await waitFor(() =>
			expect(aiModels.createModelProfile).toHaveBeenCalled(),
		);
		expect(aiModels.createModelProfile.mock.calls[0][0]).toEqual(
			expect.objectContaining({
				name: "New Chat",
				connection_id: "provider-1",
				model: "gpt-5",
				enabled_for_chat: true,
			}),
		);
		expect(aiModels.createModelProfile.mock.calls[0][0]).not.toHaveProperty(
			"max_tokens",
		);
		expect(onValueChange).toHaveBeenCalledWith("profile-2");
	});

	it("explains that a provider is required before inline creation", async () => {
		aiModels.listProviderConnections.mockResolvedValue([]);
		const { user } = renderWithProviders(
			<ModelProfileSelector value={null} onValueChange={vi.fn()} />,
		);

		await user.click(
			await screen.findByRole("button", { name: /create profile/i }),
		);

		const dialog = screen.getByRole("dialog", {
			name: "Create Model Profile",
		});
		expect(
			within(dialog).getByText("Create a provider connection first"),
		).toBeInTheDocument();
	});
});
