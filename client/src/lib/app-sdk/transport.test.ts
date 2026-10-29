import { afterEach, describe, expect, it, vi } from "vitest";
import { getPlatformAuth, type PlatformAuthBridge } from "./transport";

type G = typeof globalThis & { __BIFROST_PLATFORM_AUTH_V1__?: PlatformAuthBridge };

afterEach(() => {
	delete (globalThis as G).__BIFROST_PLATFORM_AUTH_V1__;
});

describe("getPlatformAuth", () => {
	it("returns undefined when the host has not installed a bridge", () => {
		expect(getPlatformAuth()).toBeUndefined();
	});

	it("returns the bridge the host installed, read at call time", () => {
		const bridge: PlatformAuthBridge = {
			getAccessToken: () => "tok",
			canRefreshAccessToken: () => true,
			refreshAccessToken: vi.fn(async () => true),
			handleAuthenticationFailure: vi.fn(),
		};
		(globalThis as G).__BIFROST_PLATFORM_AUTH_V1__ = bridge;
		expect(getPlatformAuth()).toBe(bridge);
	});
});
