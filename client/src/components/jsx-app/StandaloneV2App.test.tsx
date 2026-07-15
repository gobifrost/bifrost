import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	StandaloneV2App,
	type BifrostAppBootstrap,
	type StandaloneV2Module,
} from "./StandaloneV2App";

vi.mock("@/hooks/useOrgScope", () => ({
	useOrgScope: () => ({ scope: { type: "global", orgId: null } }),
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

function moduleScript(entry: string): HTMLScriptElement {
	const script = appendedScripts.find(
		(candidate) => candidate.dataset.bifrostAppEntry?.endsWith(`assets/${entry}.js`),
	);
	if (!script) throw new Error(`No module script found for ${entry}`);
	return script;
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
	localStorage.clear();
	delete window.__BIFROST_APP__;
	delete window.__BIFROST_APP_MODULES__;
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

		const stylesheet = document.querySelector<HTMLLinkElement>(
			'link[rel="stylesheet"]',
		);
		expect(stylesheet?.href).toBe(
			`${window.location.origin}/api/applications/app-1/dist/assets/main.css`,
		);
		expect(stylesheet?.href).not.toContain("?");
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
