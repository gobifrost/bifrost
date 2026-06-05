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

	it("clears the bootstrap on unmount", async () => {
		localStorage.setItem("bifrost_access_token", "tok-1");
		const { unmount } = render(<StandaloneV2App {...baseProps} />);
		await waitFor(() => expect(window.__BIFROST_APP__).toBeDefined());
		unmount();
		expect(window.__BIFROST_APP__).toBeUndefined();
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
});
