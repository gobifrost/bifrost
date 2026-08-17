import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const createLaunch = vi.fn();

vi.mock("@/hooks/useApplications", () => ({
	createIsolatedApplicationLaunch: (...args: unknown[]) =>
		createLaunch(...args),
}));

import { IsolatedSolutionApp } from "./IsolatedSolutionApp";

function LocationProbe() {
	const location = useLocation();
	return (
		<span data-testid="location">
			{location.pathname}
			{location.search}
			{location.hash}
		</span>
	);
}

function renderApp() {
	const queryClient = new QueryClient({
		defaultOptions: { queries: { retry: false } },
	});
	return render(
		<QueryClientProvider client={queryClient}>
			<MemoryRouter initialEntries={["/apps/demo/reports?period=week#top"]}>
				<LocationProbe />
				<Routes>
					<Route
						path="/apps/:applicationId/*"
						element={
							<IsolatedSolutionApp appId="app-1" appSlug="demo" />
						}
					/>
				</Routes>
			</MemoryRouter>
		</QueryClientProvider>,
	);
}

beforeEach(() => {
	createLaunch.mockReset();
	createLaunch.mockResolvedValue({
		// Keep iframe rendering inside the DOM harness. The E2E boundary proves
		// the real one-time runtime URL.
		launch_url: "data:text/html,isolated-launch",
	});
});

describe("IsolatedSolutionApp", () => {
	it("restores the visible deep link inside an opaque iframe", async () => {
		renderApp();

		const frame = await screen.findByTitle("demo");
		expect(createLaunch).toHaveBeenCalledWith(
			"app-1",
			"/reports?period=week#top",
			expect.objectContaining({ signal: expect.any(AbortSignal) }),
		);
		expect(frame).toHaveAttribute(
			"src",
			"data:text/html,isolated-launch",
		);
		expect(frame).toHaveAttribute("sandbox", "allow-forms allow-scripts");
		expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
	});

	it("mirrors only its iframe navigation back to the /apps URL", async () => {
		renderApp();
		const frame = (await screen.findByTitle("demo")) as HTMLIFrameElement;

		act(() => {
			window.dispatchEvent(
				new MessageEvent("message", {
					source: frame.contentWindow,
					data: {
						type: "bifrost:app-navigation",
						path: "/invoices/42",
						search: "?mode=review",
						hash: "#history",
					},
				}),
			);
		});

		await waitFor(() =>
			expect(screen.getByTestId("location")).toHaveTextContent(
				"/apps/demo/invoices/42?mode=review#history",
			),
		);
	});
});
