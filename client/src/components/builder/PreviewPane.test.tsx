/**
 * Tests for the builder preview pane — the unconfigured-app-origin empty state
 * (never a broken iframe), the stale badge, and the route/reload controls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { PreviewPane } from "./PreviewPane";

const onRouteChange = vi.fn();
const onReload = vi.fn();

function renderPane(props: Partial<Parameters<typeof PreviewPane>[0]> = {}) {
	return renderWithProviders(
		<PreviewPane
			appOrigin={null}
			route="/"
			onRouteChange={onRouteChange}
			onReload={onReload}
			isStale={false}
			{...props}
		/>,
	);
}

beforeEach(() => {
	onRouteChange.mockReset();
	onReload.mockReset();
});

describe("when no app origin is configured", () => {
	it("renders the unavailable state instead of an iframe", () => {
		renderPane({ appOrigin: null });

		expect(screen.getByTestId("preview-unavailable")).toBeInTheDocument();
		expect(screen.getByText(/preview unavailable/i)).toBeInTheDocument();
		expect(
			screen.getByText(/app origin is not configured/i),
		).toBeInTheDocument();
		expect(screen.queryByTestId("preview-frame")).not.toBeInTheDocument();
	});

	it("disables the reload button", () => {
		renderPane({ appOrigin: null });

		expect(screen.getByRole("button", { name: /reload preview/i })).toBeDisabled();
	});
});

describe("when an app origin is configured", () => {
	it("frames the app at the origin and route", () => {
		renderPane({ appOrigin: "https://apps.example.test", route: "/reports" });

		const frame = screen.getByTestId("preview-frame");
		expect(frame).toHaveAttribute(
			"src",
			"https://apps.example.test/reports",
		);
		expect(screen.queryByTestId("preview-unavailable")).not.toBeInTheDocument();
	});

	it("sandboxes the frame", () => {
		renderPane({ appOrigin: "https://apps.example.test" });

		expect(screen.getByTestId("preview-frame")).toHaveAttribute("sandbox");
	});

	it("reloads on demand", async () => {
		const { user } = renderPane({ appOrigin: "https://apps.example.test" });

		await user.click(screen.getByRole("button", { name: /reload preview/i }));

		expect(onReload).toHaveBeenCalled();
	});
});

describe("stale state", () => {
	it("shows the stale badge when source is ahead of the preview", () => {
		renderPane({ isStale: true });

		expect(screen.getByTestId("stale-preview-badge")).toBeInTheDocument();
	});

	it("hides the stale badge when the preview matches the source", () => {
		renderPane({ isStale: false });

		expect(screen.queryByTestId("stale-preview-badge")).not.toBeInTheDocument();
	});
});

describe("route bar", () => {
	it("reports route edits", async () => {
		const { user } = renderPane({ route: "" });

		await user.type(screen.getByLabelText(/preview route/i), "/x");

		expect(onRouteChange).toHaveBeenCalled();
	});
});
