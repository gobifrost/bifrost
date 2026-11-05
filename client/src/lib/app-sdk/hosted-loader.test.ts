import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	HostedSdkLoadError,
	getPreloadedHostedSdkFactory,
	loadHostedSdkFactory,
} from "./hosted-loader";

const fingerprint = "a".repeat(64);
const factoryId = "c".repeat(64);
const integrity = `sha256-${btoa(String.fromCharCode(...new Uint8Array(32).fill(170)))}`;

function descriptor(overrides: Record<string, unknown> = {}) {
	return {
		api_major: 1,
		factory_version: 1,
		factory_id: factoryId,
		fingerprint,
		integrity,
		asset_url: `/api/app-sdk/assets/${fingerprint}.js`,
		...overrides,
	};
}

describe("hosted SDK loader", () => {
	beforeEach(() => {
		delete window.__BIFROST_APP_SDK_FACTORIES_V1__;
		delete window.__BIFROST_APP_SDK_LOADER_V1__;
		vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(descriptor()))));
	});

	afterEach(() => vi.unstubAllGlobals());

	it("loads a same-origin immutable factory once and exposes it to another binding copy", async () => {
		const append = vi.spyOn(document.head, "appendChild").mockImplementation((node) => {
			if (!(node instanceof HTMLScriptElement)) return node;
			expect(node.src).toBe(`${window.location.origin}/api/app-sdk/assets/${fingerprint}.js`);
			expect(node.integrity).toBe(integrity);
			expect(node.crossOrigin).toBe("anonymous");
			window.__BIFROST_APP_SDK_FACTORIES_V1__ = { [factoryId]: { createSdk: vi.fn() } };
			queueMicrotask(() => node.dispatchEvent(new Event("load")));
			return node;
		});

		const [first, second] = await Promise.all([
			loadHostedSdkFactory("/"),
			loadHostedSdkFactory(window.location.origin),
		]);
		expect(first).toBe(second);
		expect(append).toHaveBeenCalledTimes(1);
		expect(getPreloadedHostedSdkFactory("/")).toBe(first);
	});

	it("keeps one origin-major selection when separately bundled host and App loaders see a newer descriptor", async () => {
		const nextFingerprint = "b".repeat(64);
		const nextIntegrity = `sha256-${btoa(String.fromCharCode(...new Uint8Array(32).fill(187)))}`;
		const fetchMock = vi.fn()
			.mockResolvedValueOnce(new Response(JSON.stringify(descriptor())))
			.mockResolvedValueOnce(new Response(JSON.stringify(descriptor({ fingerprint: nextFingerprint, integrity: nextIntegrity, asset_url: `/api/app-sdk/assets/${nextFingerprint}.js` }))));
		vi.stubGlobal("fetch", fetchMock);
		const append = vi.spyOn(document.head, "appendChild").mockImplementation((node) => {
			if (!(node instanceof HTMLScriptElement)) return node;
			window.__BIFROST_APP_SDK_FACTORIES_V1__ = { [factoryId]: { createSdk: vi.fn() } };
			queueMicrotask(() => node.dispatchEvent(new Event("load")));
			return node;
		});

		const hostLoader = await import("./hosted-loader");
		vi.resetModules();
		const appLoader = await import("./hosted-loader");
		const [hostFactory, appFactory] = await Promise.all([
			hostLoader.loadHostedSdkFactory("/"),
			appLoader.loadHostedSdkFactory("/"),
		]);
		expect(hostFactory).toBe(appFactory);
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(append).toHaveBeenCalledTimes(1);
	});

	it("rejects a descriptor whose asset is not an immutable asset on the selected origin", async () => {
		vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(descriptor({ asset_url: "https://other.example/sdk.js" })))));
		await expect(loadHostedSdkFactory("/")).rejects.toBeInstanceOf(HostedSdkLoadError);
	});

	it("rejects a descriptor whose factory id is not a SHA-256 digest", async () => {
		const response = new Response(
			JSON.stringify(descriptor({ factory_id: "factory-a" })),
		);
		vi.stubGlobal("fetch", vi.fn(async () => response));
		await expect(loadHostedSdkFactory("/")).rejects.toBeInstanceOf(HostedSdkLoadError);
	});

	it("does not poison the cache when the script load fails", async () => {
		let attempt = 0;
		vi.spyOn(document.head, "appendChild").mockImplementation((node) => {
			if (!(node instanceof HTMLScriptElement)) return node;
			attempt += 1;
			if (attempt === 1) queueMicrotask(() => node.dispatchEvent(new Event("error")));
			else {
				window.__BIFROST_APP_SDK_FACTORIES_V1__ = { [factoryId]: { createSdk: vi.fn() } };
				queueMicrotask(() => node.dispatchEvent(new Event("load")));
			}
			return node;
		});
		await expect(loadHostedSdkFactory("/")).rejects.toBeInstanceOf(HostedSdkLoadError);
		await expect(loadHostedSdkFactory("/")).resolves.toEqual(expect.objectContaining({ createSdk: expect.any(Function) }));
		expect(attempt).toBe(2);
	});
});
