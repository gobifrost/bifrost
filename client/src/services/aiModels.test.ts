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
	listModelAssignments,
	listModelProfiles,
	listProviderConnections,
	setModelAssignment,
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
		apiClient.GET
			.mockResolvedValueOnce({ data: [{ id: "provider-1" }] })
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
		apiClient.POST
			.mockResolvedValueOnce({ data: { id: "provider-1" } })
			.mockResolvedValueOnce({ data: { id: "profile-1" } });

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
			max_tokens: 16384,
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

	it("surfaces API detail messages", async () => {
		apiClient.GET.mockResolvedValueOnce({
			error: { detail: "Profile is assigned" },
		});

		await expect(listModelProfiles()).rejects.toThrow("Profile is assigned");
	});
});
