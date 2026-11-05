import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import {
	getAppSdkUpgradeSettings,
	updateAppSdkUpgradeSettings,
} from "./appSdkUpgradeSettings";

describe("app SDK upgrade settings service", () => {
	beforeEach(() => authFetchMock.mockReset());

	it("reads and updates the global automatic upgrade setting", async () => {
		authFetchMock
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ enabled: false }), { status: 200 }),
			)
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ enabled: true }), { status: 200 }),
			);

		await expect(getAppSdkUpgradeSettings()).resolves.toEqual({
			enabled: false,
		});
		await expect(updateAppSdkUpgradeSettings(true)).resolves.toEqual({
			enabled: true,
		});

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/maintenance/app-sdk-upgrades/settings",
			undefined,
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/app-sdk-upgrades/settings",
			expect.objectContaining({
				method: "PUT",
				body: '{"enabled":true}',
			}),
		);
	});

	it("surfaces API details when saving fails", async () => {
		authFetchMock.mockResolvedValue(
			new Response(JSON.stringify({ detail: "Setting is unavailable" }), {
				status: 503,
			}),
		);

		await expect(updateAppSdkUpgradeSettings(true)).rejects.toThrow(
			"Setting is unavailable",
		);
	});
});
