import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent } from "@testing-library/react";

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
	profile_count: 2,
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

const legacyProfile = {
	...profile,
	id: "profile-2",
	name: "Legacy Support Profile With A Very Long Saved Name",
	model: "openai/gpt-4.1",
	enabled_for_chat: false,
};

function createDeferred<T>() {
	let resolve!: (value: T | PromiseLike<T>) => void;
	const promise = new Promise<T>((res) => {
		resolve = res;
	});

	return { promise, resolve };
}

describe("ModelProfileSelector", () => {
	beforeEach(() => {
		aiModels.listModelProfiles.mockResolvedValue([profile, legacyProfile]);
		aiModels.listProviderConnections.mockResolvedValue([provider]);
		aiModels.createModelProfile.mockResolvedValue({
			...profile,
			id: "profile-3",
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
		expect(onValueChange).toHaveBeenCalledWith("profile-3");
	});

	it("submits the inline create form only once when triggered twice synchronously", async () => {
		const pendingCreate = createDeferred<typeof profile>();
		aiModels.createModelProfile.mockReturnValueOnce(pendingCreate.promise);

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

		const submit = screen.getByRole("button", { name: "Create" });
		fireEvent.click(submit);
		fireEvent.click(submit);

		// The mutation's mutationFn call is scheduled on a microtask by
		// react-query, so it isn't necessarily invoked synchronously with
		// mutate(). The dedup guard itself (createSubmissionLockRef) is
		// synchronous: the second click's submitCreate() bails out before
		// calling mutate() at all, so the call count settles at 1 and never
		// advances to 2 once the microtask flushes.
		await waitFor(() =>
			expect(aiModels.createModelProfile).toHaveBeenCalledTimes(1),
		);

		pendingCreate.resolve({
			...profile,
			id: "profile-3",
			name: "New Chat",
		});

		await waitFor(() =>
			expect(onValueChange).toHaveBeenCalledWith("profile-3"),
		);
	});

	it("focuses the persistent error after a failed create", async () => {
		aiModels.createModelProfile.mockRejectedValueOnce(
			new Error("Synthetic failure"),
		);
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

		const alert = await screen.findByRole("alert");
		expect(alert).toHaveTextContent("Your entries are preserved");
		await waitFor(() => expect(alert).toHaveFocus());
		expect(onValueChange).not.toHaveBeenCalled();
	});

	it("preserves the draft after failed creation and allows retry", async () => {
		aiModels.createModelProfile.mockRejectedValueOnce(
			new Error("Synthetic failure"),
		);
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

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Your entries are preserved",
		);
		expect(
			screen.getByRole("textbox", { name: "Profile Name" }),
		).toHaveValue("New Chat");
		expect(onValueChange).not.toHaveBeenCalled();
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
		expect(onValueChange).toHaveBeenCalledWith("profile-3");
	});

	it("retains a readable saved selection when chat filtering would hide it", async () => {
		renderWithProviders(
			<ModelProfileSelector
				value="profile-2"
				onValueChange={vi.fn()}
				chatOnly
			/>,
		);

		const combobox = await screen.findByRole("combobox");
		await waitFor(() =>
			expect(combobox).toHaveTextContent(
				"Legacy Support Profile With A Very Long Saved Name",
			),
		);
		expect(combobox).not.toHaveTextContent("profile-2");
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

	it("distinguishes failed profile reads and retries", async () => {
		aiModels.listModelProfiles.mockRejectedValueOnce(
			new Error("Synthetic failure"),
		);
		const { user } = renderWithProviders(
			<ModelProfileSelector onValueChange={vi.fn()} />,
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not load model profiles",
		);
		expect(screen.getByRole("combobox")).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: "Retry model profiles" }),
		);
		await waitFor(() =>
			expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
		);
		await user.click(screen.getByRole("combobox"));
		expect(
			screen.getByRole("option", { name: /Balanced Chat/ }),
		).toBeVisible();
	});

	it("does not mistake a failed provider read for missing providers", async () => {
		aiModels.listProviderConnections.mockRejectedValueOnce(
			new Error("Synthetic failure"),
		);
		const { user } = renderWithProviders(
			<ModelProfileSelector onValueChange={vi.fn()} />,
		);

		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Create profile" }),
			).toBeEnabled(),
		);
		await user.click(
			screen.getByRole("button", { name: "Create profile" }),
		);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Could not load provider connections",
		);
		expect(
			screen.queryByText("Create a provider connection first"),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
		await user.click(
			screen.getByRole("button", { name: "Retry provider connections" }),
		);
		expect(
			await screen.findByRole("textbox", { name: "Profile Name" }),
		).toBeVisible();
	});
});
