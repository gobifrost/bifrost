import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import {
	getPlatformMemorySettings,
	getUserMemorySettings,
	listMemories,
	removeMemory,
	updatePlatformMemorySettings,
	updateUserMemorySettings,
} from "./memory";

describe("memory service", () => {
	beforeEach(() => authFetchMock.mockReset());

	it("loads platform, user, and memory-list state", async () => {
		authFetchMock
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ enabled: true }), {
					status: 200,
				}),
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						platform_enabled: true,
						user_enabled: true,
						effective_enabled: true,
					}),
					{ status: 200 },
				),
			)
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ entries: [], count: 0 }), {
					status: 200,
				}),
			);

		await expect(getPlatformMemorySettings()).resolves.toEqual({
			enabled: true,
		});
		await expect(getUserMemorySettings()).resolves.toMatchObject({
			platform_enabled: true,
			user_enabled: true,
		});
		await expect(listMemories()).resolves.toEqual({
			entries: [],
			count: 0,
		});
	});

	it("updates platform and user preferences and removes an owned memory", async () => {
		authFetchMock
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ enabled: true }), {
					status: 200,
				}),
			)
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						platform_enabled: true,
						user_enabled: true,
						effective_enabled: true,
					}),
					{ status: 200 },
				),
			)
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ removed_id: "memory-1" }), {
					status: 200,
				}),
			);

		await updatePlatformMemorySettings(true);
		await updateUserMemorySettings(true);
		await removeMemory("memory-1");

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/admin/memory/settings",
			expect.objectContaining({
				method: "PUT",
				body: '{"enabled":true}',
			}),
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/memory/settings",
			expect.objectContaining({
				method: "PUT",
				body: '{"enabled":true}',
			}),
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			3,
			"/api/memory/memory-1",
			{
				method: "DELETE",
			},
		);
	});

	it("surfaces API detail messages", async () => {
		authFetchMock.mockResolvedValue(
			new Response(JSON.stringify({ detail: "Memory is disabled" }), {
				status: 409,
			}),
		);

		await expect(updateUserMemorySettings(true)).rejects.toThrow(
			"Memory is disabled",
		);
	});
});
