import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	StandaloneV2App,
	type BifrostAppBootstrap,
	type StandaloneV2Module,
} from "./StandaloneV2App";

const orgScopeState = vi.hoisted(() => ({
	scope: { type: "global" as "global" | "organization", orgId: null as string | null },
}));

const { mockAuthFetch } = vi.hoisted(() => ({ mockAuthFetch: vi.fn() }));

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

vi.mock("@/hooks/useOrgScope", () => ({
	useOrgScope: () => ({ scope: orgScopeState.scope }),
}));

function props(entry: string) {
	return {
		appId: "app-1",
		appSlug: "dash",
		isPreview: false,
		entry: `assets/${entry}.js`,
		css: null as string | null,
		baseUrl: "/api/applications/app-1/dist",
		appOrgId: null as string | null,
		runtimeContract: "mount-v1" as const,
	};
}

let appendedScripts: HTMLScriptElement[] = [];
let appendedStylesheets: HTMLLinkElement[] = [];

function moduleScript(entry: string): HTMLScriptElement {
	const script = appendedScripts.find(
		(candidate) => candidate.dataset.bifrostAppEntry?.endsWith(`assets/${entry}.js`),
	);
	if (!script) throw new Error(`No module script found for ${entry}`);
	return script;
}

function stylesheet(name: string): HTMLLinkElement {
	const link = appendedStylesheets.find((candidate) =>
		candidate.href.endsWith(`assets/${name}.css`),
	);
	if (!link) throw new Error(`No stylesheet found for ${name}`);
	return link;
}

function manifest(entry: string, css: string | null = null) {
	return {
		ok: true,
		status: 200,
		json: async () => ({
			entry: `assets/${entry}.js`,
			css: css ? `assets/${css}.css` : null,
			base_url: "/api/applications/app-1/dist",
			app_model: "standalone_v2",
			runtime_contract: "mount-v1",
		}),
	};
}

async function finishModuleLoad(
	entry: string,
	mount: StandaloneV2Module["mount"],
): Promise<HTMLScriptElement> {
	let script!: HTMLScriptElement;
	await waitFor(() => {
		script = moduleScript(entry);
	});
	(window.__BIFROST_APP_MODULES__ ??= new Map()).set(script.src, { mount });
	act(() => script.dispatchEvent(new Event("load")));
	return script;
}

beforeEach(() => {
	appendedScripts = [];
	appendedStylesheets = [];
	localStorage.clear();
	sessionStorage.clear();
	orgScopeState.scope = { type: "global", orgId: null };
	delete window.__BIFROST_APP__;
	delete window.__BIFROST_APP_MODULES__;
	mockAuthFetch.mockReset();
	vi.spyOn(console, "error").mockImplementation(() => {});
	const appendChild = document.head.appendChild.bind(document.head);
	vi.spyOn(document.head, "appendChild").mockImplementation(
		<T extends Node,>(node: T): T => {
			// happy-dom tries to fetch module scripts and immediately dispatches an
			// error. Retain them in-memory so each test controls load completion.
			if (node instanceof HTMLScriptElement) {
				appendedScripts.push(node);
				return node;
			}
			if (node instanceof HTMLLinkElement) {
				appendedStylesheets.push(node);
				return node;
			}
			return appendChild(node) as T;
		},
	);
});

afterEach(() => {
	vi.restoreAllMocks();
	delete window.__BIFROST_APP__;
	delete window.__BIFROST_APP_MODULES__;
});

describe("StandaloneV2App", () => {
	it("loads the immutable entry and stylesheet through canonical URLs", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		render(<StandaloneV2App {...props("canonical")} css="assets/main.css" />);

		let script!: HTMLScriptElement;
		await waitFor(() => {
			script = moduleScript("canonical");
		});
		expect(script.type).toBe("module");
		expect(script.src).toBe(
			`${window.location.origin}/api/applications/app-1/dist/assets/canonical.js`,
		);
		expect(script.src).not.toMatch(/[?&](m|mode|import)=/);

		const stylesheet = appendedStylesheets[0];
		expect(stylesheet.href).toBe(
			`${window.location.origin}/api/applications/app-1/dist/assets/main.css`,
		);
		expect(stylesheet.href).not.toContain("?");
	});

	it("passes isolated bootstrap to mount and calls its teardown", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const teardown = vi.fn();
		const mount = vi.fn((_el: HTMLElement, _boot: BifrostAppBootstrap) => teardown);
		const view = render(
			<StandaloneV2App
				{...props("bootstrap")}
				appOrgId="org-42"
			/>,
		);
		const root = view.getByTestId("solution-v2-app-root");

		await finishModuleLoad("bootstrap", mount);
		await waitFor(() => expect(mount).toHaveBeenCalledTimes(1));
		const [mountEl, bootstrap] = mount.mock.calls[0];
		expect(mountEl).toBe(root);
		expect(bootstrap).toMatchObject({
			token: "tok-1",
			basename: "/apps/dash",
			orgScope: "org-42",
			appId: "app-1",
		});
		expect(window.__BIFROST_APP__).toBeUndefined();

		view.unmount();
		expect(teardown).toHaveBeenCalledTimes(1);
	});

	it("mounts with the serving session and installed scope, not stale realm state", async () => {
		localStorage.setItem("bifrost_access_token", "stale-musick-token");
		sessionStorage.setItem("bifrost_embed_token", "local-serving-token");
		orgScopeState.scope = { type: "organization", orgId: "stale-musick-org" };
		const mount = vi.fn<StandaloneV2Module["mount"]>(() => vi.fn());

		render(
			<StandaloneV2App
				{...props("instance-bound-bootstrap")}
				appOrgId="local-install-org"
			/>,
		);
		await finishModuleLoad("instance-bound-bootstrap", mount);
		await waitFor(() => expect(mount).toHaveBeenCalledTimes(1));

		expect(mount.mock.calls[0][1]).toMatchObject({
			baseUrl: window.location.origin,
			token: "local-serving-token",
			orgScope: "local-install-org",
			appId: "app-1",
			basename: "/apps/dash",
		});
	});

	it("remounts when the parent supplies a newer manifest", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const firstTeardown = vi.fn();
		const secondTeardown = vi.fn();
		const firstMount = vi.fn<StandaloneV2Module["mount"]>(() => firstTeardown);
		const secondMount = vi.fn<StandaloneV2Module["mount"]>(() => secondTeardown);
		const { rerender } = render(<StandaloneV2App {...props("first")} />);

		await finishModuleLoad("first", firstMount);
		await waitFor(() => expect(firstMount).toHaveBeenCalledTimes(1));

		rerender(<StandaloneV2App {...props("second")} />);
		await finishModuleLoad("second", secondMount);

		await waitFor(() => expect(secondMount).toHaveBeenCalledTimes(1));
		expect(firstTeardown).toHaveBeenCalledTimes(1);
	});

	it("waits for both the module and stylesheet before mounting", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const mount = vi.fn<StandaloneV2Module["mount"]>(() => vi.fn());
		render(<StandaloneV2App {...props("styled")} css="assets/styled.css" />);

		await finishModuleLoad("styled", mount);
		expect(mount).not.toHaveBeenCalled();

		act(() => stylesheet("styled").dispatchEvent(new Event("load")));
		await waitFor(() => expect(mount).toHaveBeenCalledTimes(1));
	});

	it("recovers a stale JavaScript asset from a fresh manifest without reloading", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		let resolveManifest!: (value: ReturnType<typeof manifest>) => void;
		mockAuthFetch.mockReturnValueOnce(
			new Promise((resolve) => {
				resolveManifest = resolve;
			}),
		);
		const mount = vi.fn<StandaloneV2Module["mount"]>(() => vi.fn());
		render(<StandaloneV2App {...props("stale")} />);

		let stale!: HTMLScriptElement;
		await waitFor(() => {
			stale = moduleScript("stale");
		});
		act(() => stale.dispatchEvent(new Event("error")));
		expect(
			await screen.findByRole("heading", { name: "Application updated" }),
		).toBeInTheDocument();
		expect(screen.getByText("Loading the latest version…")).toBeInTheDocument();

		await act(async () => resolveManifest(manifest("fresh")));
		await finishModuleLoad("fresh", mount);
		await waitFor(() => expect(mount).toHaveBeenCalledTimes(1));
		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/applications/app-1/bundle-manifest?mode=live",
			expect.objectContaining({ cache: "no-store" }),
		);
	});

	it("recovers a stale stylesheet and mounts only the refreshed bundle", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		mockAuthFetch.mockResolvedValueOnce(manifest("fresh-css", "fresh-css"));
		const oldMount = vi.fn<StandaloneV2Module["mount"]>(() => vi.fn());
		const freshMount = vi.fn<StandaloneV2Module["mount"]>(() => vi.fn());
		render(<StandaloneV2App {...props("stale-css")} css="assets/stale-css.css" />);

		await finishModuleLoad("stale-css", oldMount);
		act(() => stylesheet("stale-css").dispatchEvent(new Event("error")));

		await finishModuleLoad("fresh-css", freshMount);
		act(() => stylesheet("fresh-css").dispatchEvent(new Event("load")));

		await waitFor(() => expect(freshMount).toHaveBeenCalledTimes(1));
		expect(oldMount).not.toHaveBeenCalled();
	});

	it("stops after one manifest refresh when the latest asset is still unavailable", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		mockAuthFetch.mockResolvedValueOnce(manifest("still-stale"));
		render(<StandaloneV2App {...props("still-stale")} />);

		let script!: HTMLScriptElement;
		await waitFor(() => {
			script = moduleScript("still-stale");
		});
		act(() => script.dispatchEvent(new Event("error")));

		expect(await screen.findByText(/Failed to load the application entry/i)).toBeInTheDocument();
		expect(mockAuthFetch).toHaveBeenCalledTimes(1);
	});

	it("uses the preview basename when requested", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const mount = vi.fn<StandaloneV2Module["mount"]>(() => vi.fn());
		render(<StandaloneV2App {...props("preview")} isPreview />);
		await finishModuleLoad("preview", mount);
		await waitFor(() => expect(mount).toHaveBeenCalled());
		expect(mount.mock.calls[0][1].basename).toBe("/apps/dash/preview");
	});

	it("does not load or mount while unauthenticated", async () => {
		render(<StandaloneV2App {...props("unauthenticated")} />);
		expect(await screen.findByText(/Not authenticated/i)).toBeInTheDocument();
		expect(
			appendedScripts.some((script) => script.src.endsWith("unauthenticated.js")),
		).toBe(false);
	});

	it("does not attach a slow module after its host mount is gone", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const mount = vi.fn<StandaloneV2Module["mount"]>(() => vi.fn());
		const view = render(<StandaloneV2App {...props("slow")} />);
		let script!: HTMLScriptElement;
		await waitFor(() => {
			script = moduleScript("slow");
		});
		view.unmount();

		(window.__BIFROST_APP_MODULES__ ??= new Map()).set(script.src, { mount });
		act(() => script.dispatchEvent(new Event("load")));
		await act(async () => Promise.resolve());
		expect(mount).not.toHaveBeenCalled();
	});

	it("reuses one evaluated module for concurrent mounts with separate roots", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const teardown = vi.fn();
		const mount = vi.fn<StandaloneV2Module["mount"]>(() => teardown);
		const first = render(
			<StandaloneV2App {...props("concurrent")} appId="app-A" appSlug="aaa" />,
		);
		const second = render(
			<StandaloneV2App {...props("concurrent")} appId="app-B" appSlug="bbb" />,
		);

		await finishModuleLoad("concurrent", mount);
		await waitFor(() => expect(mount).toHaveBeenCalledTimes(2));
		expect(mount.mock.calls[0][0]).not.toBe(mount.mock.calls[1][0]);
		expect(mount.mock.calls.map((call) => call[1].appId)).toEqual(["app-A", "app-B"]);
		expect(
			appendedScripts.filter((script) => script.src.endsWith("concurrent.js")),
		).toHaveLength(1);

		first.unmount();
		second.unmount();
		expect(teardown).toHaveBeenCalledTimes(2);
	});

	it("supports the first mount of a legacy side-effect entry without a query", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const teardown = vi.fn();
		const view = render(
			<StandaloneV2App {...props("legacy-first")} runtimeContract={null} />,
		);

		let script!: HTMLScriptElement;
		await waitFor(() => {
			script = appendedScripts.find((candidate) =>
				candidate.dataset.bifrostLegacyAppEntry?.endsWith("assets/legacy-first.js"),
			)!;
			expect(script).toBeTruthy();
		});
		expect(script.src).not.toContain("?");
		expect(window.__BIFROST_APP__?.basename).toBe("/apps/dash");
		window.__BIFROST_APP__?.registerUnmount(teardown);
		act(() => script.dispatchEvent(new Event("load")));
		await act(async () => Promise.resolve());

		view.unmount();
		expect(teardown).toHaveBeenCalledTimes(1);
	});

	it("transfers an in-flight legacy load across development StrictMode setup", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const teardown = vi.fn();
		const view = render(
			<StrictMode>
				<StandaloneV2App {...props("legacy-strict")} runtimeContract={null} />
			</StrictMode>,
		);

		await waitFor(() => {
			expect(
				appendedScripts.filter((script) =>
					script.src.endsWith("assets/legacy-strict.js"),
				),
			).toHaveLength(1);
		});
		const script = appendedScripts.find((candidate) =>
			candidate.src.endsWith("assets/legacy-strict.js"),
		)!;
		expect(document.body.contains(window.__BIFROST_APP__?.mountEl ?? null)).toBe(true);
		window.__BIFROST_APP__?.registerUnmount(teardown);
		act(() => script.dispatchEvent(new Event("load")));
		await act(async () => Promise.resolve());

		view.unmount();
		expect(teardown).toHaveBeenCalledTimes(1);
	});

	it("rejects a concurrent mount of the same legacy side-effect entry", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const first = render(
			<StandaloneV2App {...props("legacy-concurrent")} runtimeContract={null} />,
		);
		const second = render(
			<StandaloneV2App {...props("legacy-concurrent")} runtimeContract={null} />,
		);

		const message = await second.findByText(
			/legacy Apps v2 entry cannot be mounted concurrently/i,
		);
		expect(message).toHaveTextContent(/cannot be mounted concurrently/i);
		expect(
			appendedScripts.filter((script) =>
				script.src.endsWith("assets/legacy-concurrent.js"),
			),
		).toHaveLength(1);
		const script = appendedScripts.find((candidate) =>
			candidate.src.endsWith("assets/legacy-concurrent.js"),
		)!;
		act(() => script.dispatchEvent(new Event("load")));
		await act(async () => Promise.resolve());
		first.unmount();
		second.unmount();
	});
});
