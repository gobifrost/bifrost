import { beforeEach, describe, expect, it, vi } from "vitest";

const apiClient = vi.hoisted(() => ({
	GET: vi.fn(),
	POST: vi.fn(),
	PUT: vi.fn(),
	DELETE: vi.fn(),
	PATCH: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({ apiClient }));

import {
	createModelProfile,
	createProviderConnection,
	listProviderModels,
	listModelAssignments,
	listModelProfiles,
	listProviderConnections,
	mergeModelProfiles,
	setModelAssignment,
	verifyProviderConnection,
} from "./aiModels";

describe("aiModels service", () => {
	beforeEach(() => {
		apiClient.GET.mockReset();
		apiClient.POST.mockReset();
		apiClient.PUT.mockReset();
		apiClient.DELETE.mockReset();
		apiClient.PATCH.mockReset();
	});

	it("lists provider connections, profiles, and assignments", async () => {
		apiClient.GET.mockResolvedValueOnce({ data: [{ id: "provider-1" }] })
			.mockResolvedValueOnce({ data: [{ id: "profile-1" }] })
			.mockResolvedValueOnce({ data: [{ assignment_key: "primary" }] });

		await expect(listProviderConnections()).resolves.toEqual([
			{ id: "provider-1" },
		]);
		await expect(listModelProfiles()).resolves.toEqual([
			{ id: "profile-1" },
		]);
		await expect(listModelAssignments()).resolves.toEqual([
			{ assignment_key: "primary" },
		]);

		expect(apiClient.GET).toHaveBeenNthCalledWith(
			1,
			"/api/admin/ai/connections",
		);
		expect(apiClient.GET).toHaveBeenNthCalledWith(
			2,
			"/api/admin/ai/profiles",
		);
		expect(apiClient.GET).toHaveBeenNthCalledWith(
			3,
			"/api/admin/ai/assignments",
		);
	});

	it("creates provider connections and profiles with generated paths", async () => {
		apiClient.POST.mockResolvedValueOnce({
			data: { id: "provider-1" },
		}).mockResolvedValueOnce({ data: { id: "profile-1" } });

		await createProviderConnection({
			name: "Default",
			provider: "openai",
			api_key: "secret",
			endpoint: null,
		});
		await createModelProfile({
			name: "Fast",
			connection_id: "provider-1",
			model: "gpt-5-mini",
			capabilities: null,
			enabled_for_chat: true,
		});

		expect(apiClient.POST).toHaveBeenNthCalledWith(
			1,
			"/api/admin/ai/connections",
			expect.objectContaining({
				body: expect.objectContaining({ api_key: "secret" }),
			}),
		);
		expect(apiClient.POST).toHaveBeenNthCalledWith(
			2,
			"/api/admin/ai/profiles",
			expect.objectContaining({
				body: expect.objectContaining({ enabled_for_chat: true }),
			}),
		);
	});

	it("verifies unsaved credentials and lists models for a saved provider", async () => {
		apiClient.POST.mockResolvedValueOnce({
			data: { success: true, message: "Connected" },
		});
		apiClient.GET.mockResolvedValueOnce({
			data: { provider: "openai", models: [{ id: "gpt-5-mini" }] },
		});

		await verifyProviderConnection({
			name: "OpenAI",
			provider: "openai",
			api_key: "secret",
			endpoint: "https://api.openai.com/v1",
		});
		await listProviderModels("provider-1");

		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/admin/ai/connections/verify",
			expect.objectContaining({
				body: expect.objectContaining({ api_key: "secret" }),
			}),
		);
		expect(apiClient.GET).toHaveBeenCalledWith(
			"/api/admin/ai/connections/{connection_id}/models",
			expect.objectContaining({
				params: { path: { connection_id: "provider-1" } },
			}),
		);
	});

	it("assigns profiles by assignment key", async () => {
		apiClient.PUT.mockResolvedValueOnce({
			data: { assignment_key: "primary", profile_id: "profile-1" },
		});

		await setModelAssignment("primary", "profile-1");

		expect(apiClient.PUT).toHaveBeenCalledWith(
			"/api/admin/ai/assignments/{assignment_key}",
			expect.objectContaining({
				params: { path: { assignment_key: "primary" } },
				body: { profile_id: "profile-1" },
			}),
		);
	});

	it("merges selected profiles into a target", async () => {
		apiClient.POST.mockResolvedValueOnce({
			data: {
				profile: { id: "profile-target" },
				merged_profile_ids: ["profile-source"],
				reassigned_agent_count: 2,
				reassigned_assignment_keys: ["primary"],
			},
		});

		await mergeModelProfiles({
			profile_ids: ["profile-source", "profile-target"],
			target_profile_id: "profile-target",
		});

		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/admin/ai/profiles/merge",
			{
				body: {
					profile_ids: ["profile-source", "profile-target"],
					target_profile_id: "profile-target",
				},
			},
		);
	});

	it("surfaces API detail messages", async () => {
		apiClient.GET.mockResolvedValueOnce({
			error: { detail: "Profile is assigned" },
		});

		await expect(listModelProfiles()).rejects.toThrow(
			"Profile is assigned",
		);
	});
});
