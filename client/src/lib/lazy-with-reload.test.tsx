import { render, screen, waitFor } from "@testing-library/react";
import { Component, type ErrorInfo, type ReactNode, Suspense } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { lazyWithReload } from "./lazy-with-reload";

const { mockRequestApplicationReload } = vi.hoisted(() => ({
	mockRequestApplicationReload: vi.fn(),
}));

vi.mock("./application-update", () => ({
	requestApplicationReload: (...args: unknown[]) =>
		mockRequestApplicationReload(...args),
}));

class TestErrorBoundary extends Component<
	{ children: ReactNode },
	{ error: Error | null }
> {
	state = { error: null };

	static getDerivedStateFromError(error: Error) {
		return { error };
	}

	componentDidCatch(_error: Error, _info: ErrorInfo) {}

	render() {
		return this.state.error ? <div>route failed</div> : this.props.children;
	}
}

describe("lazyWithReload", () => {
	beforeEach(() => {
		mockRequestApplicationReload.mockReset();
		vi.spyOn(console, "error").mockImplementation(() => {});
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("keeps the failed route pending while the first stale chunk reloads", async () => {
		mockRequestApplicationReload.mockReturnValue(true);
		const Page = lazyWithReload(() => Promise.reject(new Error("missing chunk")));

		render(
			<TestErrorBoundary>
				<Suspense fallback={<div>loading route</div>}>
					<Page />
				</Suspense>
			</TestErrorBoundary>,
		);

		await waitFor(() =>
			expect(mockRequestApplicationReload).toHaveBeenCalledTimes(1),
		);
		expect(screen.getByText("loading route")).toBeInTheDocument();
		expect(screen.queryByText("route failed")).not.toBeInTheDocument();
	});

	it("surfaces a second failure instead of entering a reload loop", async () => {
		mockRequestApplicationReload.mockReturnValue(false);
		const Page = lazyWithReload(() => Promise.reject(new Error("broken chunk")));

		render(
			<TestErrorBoundary>
				<Suspense fallback={<div>loading route</div>}>
					<Page />
				</Suspense>
			</TestErrorBoundary>,
		);

		expect(await screen.findByText("route failed")).toBeInTheDocument();
		expect(mockRequestApplicationReload).toHaveBeenCalledTimes(1);
	});
});
