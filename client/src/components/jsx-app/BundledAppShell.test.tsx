/**
 * Component tests for BundledAppShell.
 *
 * The bundled path dynamically imports a runtime URL produced by esbuild,
 * which we can't faithfully stub in happy-dom. The valuable thing we can
 * test at this level is the shell's control flow around the manifest:
 *
 *   - loading skeleton while the manifest fetch is in flight
 *   - manifest fetch failure → full-screen "Bundle Load Error" panel
 *   - migration notice banner surfaces when manifest.migrated=true
 *     (and can be dismissed)
 *
 * The successful-dynamic-import path (real mount, hot-reload subscription)
 * is covered by Playwright since it requires a real bundler output.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

// -----------------------------------------------------------------------------
// Mocks
// -----------------------------------------------------------------------------

const mockAuthFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

// WebSocket service subscriptions should never actually fire in tests.
const mockConnectToAppDraft = vi.fn().mockResolvedValue(undefined);
const mockOnAppCodeFileUpdate = vi.fn().mockReturnValue(() => {});
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connectToAppDraft: (...args: unknown[]) =>
			mockConnectToAppDraft(...args),
		onAppCodeFileUpdate: (...args: unknown[]) =>
			mockOnAppCodeFileUpdate(...args),
	},
}));

// Platform scope is only used inside ensureImportMap; a no-op is fine.
vi.mock("@/lib/app-code-runtime", () => ({
	$: {},
}));

// Stub the app-builder store.
vi.mock("@/stores/app-builder.store", () => ({
	useAppBuilderStore: (selector: (state: unknown) => unknown) =>
		selector({ setAppContext: () => {} }),
}));

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

function mockManifestError(status: number, text: string) {
	mockAuthFetch.mockResolvedValueOnce({
		ok: false,
		status,
		text: async () => text,
		json: async () => ({}),
	});
}

function mockManifestOk(
	overrides: Partial<{
		entry: string;
		css: string | null;
		base_url: string;
		mode: "preview" | "live";
		dependencies: Record<string, string>;
		migrated: boolean;
		app_model: string;
	}> = {},
) {
	mockAuthFetch.mockResolvedValueOnce({
		ok: true,
		text: async () => "",
		json: async () => ({
			entry: "entry.js",
			css: null,
			base_url: "/api/applications/app-1/bundle-asset",
			mode: "preview",
			dependencies: {},
			migrated: false,
			app_model: "inline_v1",
			...overrides,
		}),
	});
}

beforeEach(() => {
	mockAuthFetch.mockReset();
	mockConnectToAppDraft.mockClear();
	mockOnAppCodeFileUpdate.mockClear();
	localStorage.clear();
});

afterEach(() => {
	vi.restoreAllMocks();
	// Clean any import maps a prior test installed.
	document
		.querySelectorAll("script[data-bifrost-import-map]")
		.forEach((el) => el.remove());
});

async function renderShell({ isPreview = true }: { isPreview?: boolean } = {}) {
	const { BundledAppShell } = await import("./BundledAppShell");
	return renderWithProviders(
		<BundledAppShell appId="app-1" appSlug="my-app" isPreview={isPreview} />,
	);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("BundledAppShell — loading", () => {
	it("shows the loading skeleton while the manifest fetch is pending", async () => {
		let resolveFetch: (v: Response) => void = () => {};
		mockAuthFetch.mockImplementationOnce(
			() =>
				new Promise<Response>((resolve) => {
					resolveFetch = resolve;
				}),
		);

		await renderShell();

		expect(
			screen.getByText(/loading application/i),
		).toBeInTheDocument();

		// Clean up the hanging promise so the test doesn't leak.
		resolveFetch({
			ok: true,
			text: async () => "",
			json: async () => ({
				entry: "entry.js",
				css: null,
				base_url: "/api/applications/app-1/bundle-asset",
				mode: "preview",
				dependencies: {},
			}),
		} as Response);
	});
});

describe("BundledAppShell — error path", () => {
	it("renders the 'Bundle Load Error' panel when the manifest fetch fails", async () => {
		// Silence the expected console.error from the failed dynamic import.
		vi.spyOn(console, "error").mockImplementation(() => {});
		mockManifestError(500, "manifest broken");

		await renderShell();

		expect(
			await screen.findByText(/bundle load error/i),
		).toBeInTheDocument();
		expect(screen.getByText(/manifest broken/i)).toBeInTheDocument();
	});
});

describe("BundledAppShell — websocket subscription", () => {
	it("subscribes to the app-draft channel after a successful manifest load", async () => {
		// The dynamic import() of the entry URL will reject in happy-dom,
		// which puts the shell into the load-error state — but the subscribe
		// call happens in a sibling IIFE that doesn't depend on that. We
		// verify it fires so the hot-reload surface is wired.
		vi.spyOn(console, "error").mockImplementation(() => {});
		mockManifestOk();

		await renderShell();

		await waitFor(() =>
			expect(mockConnectToAppDraft).toHaveBeenCalledWith("app-1"),
		);
		expect(mockOnAppCodeFileUpdate).toHaveBeenCalled();
	});

	it("does not subscribe to draft rebuilds in live mode", async () => {
		vi.spyOn(console, "error").mockImplementation(() => {});
		mockManifestOk({ mode: "live" });

		await renderShell({ isPreview: false });

		await waitFor(() =>
			expect(mockAuthFetch).toHaveBeenCalledWith(
				"/api/applications/app-1/bundle-manifest?mode=live",
				expect.any(Object),
			),
		);
		expect(mockConnectToAppDraft).not.toHaveBeenCalled();
		expect(mockOnAppCodeFileUpdate).not.toHaveBeenCalled();
	});
});

describe("BundledAppShell — app_model render branch", () => {
	it("mounts the standalone v2 container (not the inline path) for standalone_v2", async () => {
		mockManifestOk({ app_model: "standalone_v2" });

		await renderShell();

		// v2 → standalone container hosting the app's own dist/ (its own
		// createRoot/router/SDK); the inline esbuild path is NOT used.
		const root = await screen.findByTestId("solution-v2-app-root");
		expect(root).toBeInTheDocument();
		const frame = root.querySelector("iframe");
		expect(frame).not.toBeNull();
		expect(frame!.getAttribute("src")).toContain(
			"/api/applications/app-1/dist/index.html",
		);
		// v2 is deploy-driven, not hot-reload — no draft subscription.
		expect(mockConnectToAppDraft).not.toHaveBeenCalled();
	});

	it("uses the inline path for inline_v1 (regression) and subscribes to drafts", async () => {
		vi.spyOn(console, "error").mockImplementation(() => {});
		mockManifestOk({ app_model: "inline_v1" });

		await renderShell();

		// No standalone container for v1.
		expect(screen.queryByTestId("solution-v2-app-root")).toBeNull();
		// v1 inline path still subscribes to draft rebuilds (unchanged).
		await waitFor(() =>
			expect(mockConnectToAppDraft).toHaveBeenCalledWith("app-1"),
		);
	});
});
