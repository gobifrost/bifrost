/**
 * Tests for the builder preview pane — the unconfigured-app-origin empty state
 * (never a broken iframe), the stale badge, and the route/reload controls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";
import { PreviewPane } from "./PreviewPane";

const onRouteChange = vi.fn();
const onReload = vi.fn();

function renderPane(props: Partial<Parameters<typeof PreviewPane>[0]> = {}) {
	return renderWithProviders(
		<PreviewPane
			launchUrl={null}
			state="unconfigured"
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
		renderPane();

		expect(screen.getByTestId("preview-unavailable")).toBeInTheDocument();
		expect(screen.getByText(/preview unavailable/i)).toBeInTheDocument();
		expect(
			screen.getByText(/separate app host is not configured/i),
		).toBeInTheDocument();
		expect(screen.queryByTestId("preview-frame")).not.toBeInTheDocument();
	});

	it("disables the reload button", () => {
		renderPane();

		expect(
			screen.getByRole("button", { name: /reload preview/i }),
		).toBeDisabled();
	});
});

describe("when an app origin is configured", () => {
	it("frames the exact one-time launch URL returned by the backend", () => {
		renderPane({
			launchUrl: "https://apps.example.test/launch/one-time-code",
			state: "ready",
			route: "/reports",
		});

		const frame = screen.getByTestId("preview-frame");
		expect(frame).toHaveAttribute(
			"src",
			"https://apps.example.test/launch/one-time-code",
		);
		expect(
			screen.queryByTestId("preview-unavailable"),
		).not.toBeInTheDocument();
	});

	it("sandboxes the frame", () => {
		renderPane({
			launchUrl: "https://apps.example.test/launch/code",
			state: "ready",
		});

		expect(screen.getByTestId("preview-frame")).toHaveAttribute("sandbox");
	});

	it("explains both secure-session restoration and document loading", async () => {
		const { rerender } = renderPane({ state: "loading" });

		expect(
			screen.getByRole("status", { name: /restoring your preview/i }),
		).toHaveTextContent(/secure app session/i);

		rerender(
			<PreviewPane
				launchUrl="https://apps.example.test/launch/code"
				state="ready"
				route="/"
				onRouteChange={onRouteChange}
				onReload={onReload}
				isStale={false}
			/>,
		);

		const frame = screen.getByTitle("App preview");
		expect(
			screen.getByRole("status", { name: /starting your preview/i }),
		).toHaveTextContent(/loading the saved app/i);

		fireEvent.load(frame);
		await waitFor(() =>
			expect(
				screen.queryByRole("status", {
					name: /starting your preview/i,
				}),
			).not.toBeInTheDocument(),
		);
	});

	it("reloads on demand", async () => {
		const { user } = renderPane({
			launchUrl: "https://apps.example.test/launch/code",
			state: "ready",
		});

		await user.click(
			screen.getByRole("button", { name: /reload preview/i }),
		);

		expect(onReload).toHaveBeenCalled();
	});

	it("switches to a familiar mobile preview viewport", async () => {
		const { user } = renderPane({
			launchUrl: "https://apps.example.test/launch/code",
			state: "ready",
		});

		await user.click(
			screen.getByRole("button", { name: /mobile preview/i }),
		);

		expect(
			screen.getByRole("button", { name: /mobile preview/i }),
		).toHaveAttribute("aria-pressed", "true");
		expect(screen.getByTestId("preview-frame").parentElement).toHaveClass(
			"w-[390px]",
		);
	});
});

describe("stale state", () => {
	it("shows the stale badge when source is ahead of the preview", () => {
		renderPane({ isStale: true });

		expect(screen.getByTestId("stale-preview-badge")).toBeInTheDocument();
	});

	it("hides the stale badge when the preview matches the source", () => {
		renderPane({ isStale: false });

		expect(
			screen.queryByTestId("stale-preview-badge"),
		).not.toBeInTheDocument();
	});
});

describe("route bar", () => {
	it("requests a fresh launch only after route submission", async () => {
		const { user } = renderPane({
			launchUrl: "https://apps.example.test/launch/code",
			state: "ready",
		});

		const routeInput = screen.getByRole("textbox", {
			name: "Preview route",
		});
		await user.clear(routeInput);
		await user.type(routeInput, "reports/quarterly");
		expect(onRouteChange).not.toHaveBeenCalled();
		await user.click(
			screen.getByRole("button", { name: /open preview route/i }),
		);

		expect(onRouteChange).toHaveBeenCalledWith("/reports/quarterly");
	});
});

describe("deployment and launch states", () => {
	it("explains that source is saved while the first deploy is pending", () => {
		renderPane({ state: "waiting" });

		expect(
			screen.getByText(/preview is not deployed yet/i),
		).toBeInTheDocument();
		expect(screen.getByText(/source is saved/i)).toBeInTheDocument();
	});

	it("shows a backend launch failure without framing a guessed URL", () => {
		renderPane({
			state: "failed",
			errorMessage: "The launch code service is unavailable",
		});

		expect(
			screen.getByText("The launch code service is unavailable"),
		).toBeInTheDocument();
		expect(screen.queryByTestId("preview-frame")).not.toBeInTheDocument();
	});
});
