import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HostedSdk } from "./hosted-contract";

const loader = vi.hoisted(() => ({
	preloaded: undefined as { createSdk: (peers: unknown) => HostedSdk } | undefined,
	load: vi.fn(),
}));

vi.mock("./hosted-loader", () => ({
	getPreloadedHostedSdkFactory: () => loader.preloaded,
	hostedSdkOrigin: (baseUrl: string) => new URL(baseUrl, window.location.href).origin,
	loadHostedSdkFactory: (...args: unknown[]) => loader.load(...args),
}));

import { BifrostProvider, tables, useBifrostContext } from "./hosted-binding";

function sdkFixture(baseUrl = "https://api.example") {
	const subscribe = vi.fn(() => vi.fn());
	const sdk = {
		BifrostProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
		useBifrostContext: () => ({ baseUrl, token: "token" }),
		tables: { subscribe },
		files: {},
	} as unknown as HostedSdk;
	return { sdk, subscribe };
}

function ContextReader() {
	return <output>{useBifrostContext().baseUrl}</output>;
}

describe("hosted SDK binding", () => {
	afterEach(() => {
		loader.preloaded = undefined;
		loader.load.mockReset();
		vi.restoreAllMocks();
	});

	it("clears a failed same-origin imperative load so a later explicit call can load the factory", async () => {
		vi.resetModules();
		const binding = await import("./hosted-binding");
		loader.load.mockRejectedValueOnce(new Error("offline"));
		await expect(binding.tables.count("table-1")).rejects.toThrow("offline");
		const count = vi.fn(async () => 3);
		loader.load.mockResolvedValue({ createSdk: () => ({ tables: { count } } as unknown as HostedSdk) });
		await expect(binding.tables.count("table-1")).resolves.toBe(3);
		expect(loader.load).toHaveBeenCalledTimes(2);
	});

	it("reuses a pending same-origin imperative SDK instance when the provider mounts", async () => {
		vi.resetModules();
		const binding = await import("./hosted-binding");
		let resolveFactory!: (factory: { createSdk: (peers: unknown) => HostedSdk }) => void;
		loader.load.mockReturnValue(new Promise((resolve) => { resolveFactory = resolve; }));
		const count = vi.fn(async () => 3);
		const subscribe = vi.fn(() => vi.fn());
		const sdk = {
			BifrostProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
			useBifrostContext: () => ({ baseUrl: window.location.origin, token: "token" }),
			tables: { count, subscribe },
			files: {},
		} as unknown as HostedSdk;
		const createSdk = vi.fn(() => sdk);
		const countPromise = binding.tables.count("table-1");
		binding.tables.subscribe("table-1", null, vi.fn());
		const Reader = () => <output>{binding.useBifrostContext().baseUrl}</output>;
		render(
			<binding.BifrostProvider baseUrl={window.location.origin} token="token">
				<Reader />
			</binding.BifrostProvider>,
		);

		expect(loader.load).toHaveBeenCalledTimes(1);
		resolveFactory({ createSdk });
		await expect(countPromise).resolves.toBe(3);
		await expect(screen.findByText(window.location.origin)).resolves.toBeInTheDocument();
		expect(createSdk).toHaveBeenCalledTimes(1);
		expect(subscribe).toHaveBeenCalledTimes(1);
	});

	it("returns an immediate cancellation closure while a pre-provider subscription waits", async () => {
		let resolveFactory!: (factory: { createSdk: (peers: unknown) => HostedSdk }) => void;
		loader.load.mockReturnValue(new Promise((resolve) => { resolveFactory = resolve; }));
		const cancel = tables.subscribe("table-1", null, vi.fn());
		cancel();
		const { sdk, subscribe } = sdkFixture();
		resolveFactory({ createSdk: () => sdk });
		await waitFor(() => expect(loader.load).toHaveBeenCalled());
		await Promise.resolve();
		expect(subscribe).not.toHaveBeenCalled();
	});

	it("gates children until the hosted provider can use the App React instance", async () => {
		const { sdk } = sdkFixture();
		const createSdk = vi.fn(() => sdk);
		loader.load.mockResolvedValue({ createSdk });

		render(
			<BifrostProvider baseUrl="https://api.example" token="token">
				<ContextReader />
			</BifrostProvider>,
		);

		expect(screen.getByText("Loading App...")).toBeInTheDocument();
		await expect(screen.findByText("https://api.example")).resolves.toBeInTheDocument();
		expect(createSdk).toHaveBeenCalledWith(expect.objectContaining({ errors: expect.any(Object) }));
	});

	it("uses a preloaded factory to create the provider SDK once", async () => {
		vi.resetModules();
		const binding = await import("./hosted-binding");
		const { sdk } = sdkFixture();
		const createSdk = vi.fn(() => sdk);
		loader.preloaded = { createSdk };
		const Reader = () => <output>{binding.useBifrostContext().baseUrl}</output>;

		render(
			<binding.BifrostProvider baseUrl="https://api.example" token="token">
				<Reader />
			</binding.BifrostProvider>,
		);

		await expect(screen.findByText("https://api.example")).resolves.toBeInTheDocument();
		expect(createSdk).toHaveBeenCalledTimes(1);
		expect(loader.load).not.toHaveBeenCalled();
	});

	it("uses the configured remote provider SDK for imperative calls", async () => {
		vi.resetModules();
		const binding = await import("./hosted-binding");
		const count = vi.fn(async () => 7);
		const { sdk } = sdkFixture("https://remote.example");
		sdk.tables.count = count;
		loader.load.mockResolvedValue({ createSdk: () => sdk });

		const Reader = () => <output>{binding.useBifrostContext().baseUrl}</output>;
		render(
			<binding.BifrostProvider baseUrl="https://remote.example" token="token">
				<Reader />
			</binding.BifrostProvider>,
		);

		await expect(screen.findByText("https://remote.example")).resolves.toBeInTheDocument();
		await expect(binding.tables.count("table-1")).resolves.toBe(7);
		expect(count).toHaveBeenCalledWith("table-1");
		expect(loader.load).toHaveBeenCalledTimes(1);
		expect(loader.load).toHaveBeenCalledWith("https://remote.example");
	});

	it("gates a changed provider origin, ignores its stale completion, and recovers on a later origin", async () => {
		let resolveA!: (factory: { createSdk: (peers: unknown) => HostedSdk }) => void;
		let rejectB!: (reason: Error) => void;
		loader.load.mockImplementation((baseUrl: string) => {
			if (baseUrl === "https://a.example") {
				return new Promise((resolve) => { resolveA = resolve; });
			}
			if (baseUrl === "https://b.example") {
				return new Promise((_resolve, reject) => { rejectB = reject; });
			}
			return Promise.resolve({ createSdk: () => sdkFixture("https://c.example").sdk });
		});
		vi.spyOn(console, "error").mockImplementation(() => {});

		const view = render(
			<BifrostProvider baseUrl="https://a.example" token="token"><ContextReader /></BifrostProvider>,
		);
		await waitFor(() => expect(loader.load).toHaveBeenCalledWith("https://a.example"));
		view.rerender(
			<BifrostProvider baseUrl="https://b.example" token="token"><ContextReader /></BifrostProvider>,
		);
		await waitFor(() => expect(loader.load).toHaveBeenCalledWith("https://b.example"));
		expect(screen.getByText("Loading App...")).toBeInTheDocument();
		rejectB(new Error("offline"));
		await expect(screen.findByRole("alert")).resolves.toHaveTextContent("Unable to load this App. Reload and try again.");
		resolveA({ createSdk: () => sdkFixture("https://a.example").sdk });
		await Promise.resolve();
		expect(screen.queryByText("https://a.example")).not.toBeInTheDocument();

		view.rerender(
			<BifrostProvider baseUrl="https://c.example" token="token"><ContextReader /></BifrostProvider>,
		);
		await expect(screen.findByText("https://c.example")).resolves.toBeInTheDocument();
	});
});
