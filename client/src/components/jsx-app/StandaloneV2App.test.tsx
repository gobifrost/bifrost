import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StandaloneV2App } from "./StandaloneV2App";

// useOrgScope reads a zustand store; stub it to a stable global scope.
vi.mock("@/hooks/useOrgScope", () => ({
	useOrgScope: () => ({ scope: { type: "global", orgId: null } }),
}));

const baseProps = {
	appId: "app-1",
	appSlug: "dash",
	isPreview: false,
	entry: "assets/main-abc.js",
	css: null as string | null,
	baseUrl: "/api/applications/app-1/dist",
	appOrgId: null as string | null,
};

beforeEach(() => {
	localStorage.clear();
	delete window.__BIFROST_APP__;
	// import() of the dist entry rejects in happy-dom; that's fine — we assert on
	// the bootstrap, not the app actually booting.
	vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
	vi.restoreAllMocks();
	delete window.__BIFROST_APP__;
});

describe("StandaloneV2App", () => {
	it("injects window.__BIFROST_APP__ with token, basename, and mount element", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		render(<StandaloneV2App {...baseProps} />);

		const root = screen.getByTestId("solution-v2-app-root");
		await waitFor(() => expect(window.__BIFROST_APP__).toBeDefined());
		const boot = window.__BIFROST_APP__!;
		expect(boot.token).toBe("tok-1");
		expect(boot.basename).toBe("/apps/dash"); // live mode (not preview)
		expect(boot.mountEl).toBe(root);
		expect(boot.orgScope).toBeNull();
	});

	it("uses the /preview basename in preview mode", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		render(<StandaloneV2App {...baseProps} isPreview />);
		await waitFor(() => expect(window.__BIFROST_APP__).toBeDefined());
		expect(window.__BIFROST_APP__!.basename).toBe("/apps/dash/preview");
	});

	it("prefers the app's own org scope when org-scoped", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		render(<StandaloneV2App {...baseProps} appOrgId="org-42" />);
		await waitFor(() => expect(window.__BIFROST_APP__).toBeDefined());
		expect(window.__BIFROST_APP__!.orgScope).toBe("org-42");
	});

	it("shows an error and injects nothing when unauthenticated", async () => {
		render(<StandaloneV2App {...baseProps} />);
		expect(await screen.findByText(/Not authenticated/i)).toBeInTheDocument();
		expect(window.__BIFROST_APP__).toBeUndefined();
	});

	it("disables (does not delete) the bootstrap on unmount so a late import can't reach the live mount node", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const { unmount } = render(<StandaloneV2App {...baseProps} />);
		await waitFor(() => expect(window.__BIFROST_APP__).toBeDefined());
		const liveMount = window.__BIFROST_APP__!.mountEl;
		unmount();
		// The bootstrap is intentionally LEFT in place (a tombstone) so the
		// scaffold's `boot?.mountEl ?? getElementById("root")` never falls back
		// to the platform root for a late-resolving entry. But it no longer
		// points at the (now-detached) live mount node.
		expect(window.__BIFROST_APP__).toBeDefined();
		expect(window.__BIFROST_APP__!.mountEl).not.toBe(liveMount);
		expect(document.body.contains(window.__BIFROST_APP__!.mountEl)).toBe(false);
	});

	it("busts the ES module cache so a remount re-runs the entry", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const first = render(<StandaloneV2App {...baseProps} />);
		const root1 = first.getByTestId("solution-v2-app-root");
		await waitFor(() => expect(root1.dataset.bifrostEntry).toBeTruthy());
		const url1 = root1.dataset.bifrostEntry!;
		expect(url1).toContain("assets/main-abc.js");
		expect(url1).toMatch(/[?&]m=\d+/); // cache-bust present
		first.unmount();

		// A fresh mount of the same app must use a DIFFERENT import URL (new
		// nonce) so the browser re-executes the entry instead of returning the
		// cached module.
		const second = render(<StandaloneV2App {...baseProps} />);
		const root2 = second.getByTestId("solution-v2-app-root");
		await waitFor(() => expect(root2.dataset.bifrostEntry).toBeTruthy());
		expect(root2.dataset.bifrostEntry).not.toBe(url1);
	});

	it("calls the app-registered unmount teardown on cleanup (no leak)", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const { unmount } = render(<StandaloneV2App {...baseProps} />);
		await waitFor(() => expect(window.__BIFROST_APP__).toBeDefined());

		// Simulate the app registering its root teardown after createRoot.
		const teardown = vi.fn();
		window.__BIFROST_APP__!.registerUnmount(teardown);

		unmount();
		expect(teardown).toHaveBeenCalledTimes(1);
	});

	it("does not let a late-resolving entry mount into the platform root after unmount (R7-P2-e)", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		// The platform root the scaffold falls back to via getElementById("root").
		const platformRoot = document.createElement("div");
		platformRoot.id = "root";
		document.body.appendChild(platformRoot);
		try {
			const { unmount } = render(<StandaloneV2App {...baseProps} />);
			await waitFor(() => expect(window.__BIFROST_APP__).toBeDefined());

			// User navigates away BEFORE the dynamic import resolves.
			unmount();

			// Now the in-flight entry chunk finally executes its top-level code,
			// exactly as the scaffold's main.tsx does:
			//   const boot = window.__BIFROST_APP__;
			//   const mountEl = boot?.mountEl ?? document.getElementById("root")!;
			//   boot?.registerUnmount?.(() => root.unmount());
			const boot = window.__BIFROST_APP__;
			const mountEl = boot?.mountEl ?? document.getElementById("root")!;
			const rootUnmount = vi.fn();
			boot?.registerUnmount?.(rootUnmount);

			// The late entry must NOT have resolved its mount node to the
			// platform root — otherwise it mounts a stale app over the shell.
			expect(mountEl).not.toBe(platformRoot);
			// And if it did mount, the tombstone's registerUnmount tore it down
			// immediately so nothing stays alive.
			expect(rootUnmount).toHaveBeenCalledTimes(1);
		} finally {
			platformRoot.remove();
		}
	});
});
