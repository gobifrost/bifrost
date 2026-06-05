import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { RouteTransitionProgress } from "./RouteTransitionProgress";

function TestRoutes() {
	return (
		<>
			<RouteTransitionProgress />
			<Link to="/chat">Chat</Link>
			<a href="https://example.com/docs">External docs</a>
			<Routes>
				<Route path="/" element={<div>Dashboard</div>} />
				<Route path="/chat" element={<div>Chat page</div>} />
			</Routes>
		</>
	);
}

describe("RouteTransitionProgress", () => {
	it("shows progress immediately for same-origin route clicks", async () => {
		vi.useFakeTimers();

		try {
			render(
				<MemoryRouter initialEntries={["/"]}>
					<TestRoutes />
				</MemoryRouter>,
			);

			fireEvent.click(screen.getByRole("link", { name: "Chat" }));

			expect(
				screen.getByRole("progressbar", { name: "Loading page" }),
			).toBeInTheDocument();
			const fill = screen
				.getByRole("progressbar", { name: "Loading page" })
				.querySelector(".route-transition-progress-fill");
			expect(fill).toBeInTheDocument();
			expect(fill).toHaveAttribute("data-state");
			expect(screen.getByText("Chat page")).toBeInTheDocument();

			act(() => {
				vi.advanceTimersByTime(220);
			});

			expect(
				screen.queryByRole("progressbar", { name: "Loading page" }),
			).not.toBeInTheDocument();
		} finally {
			vi.useRealTimers();
		}
	});

	it("ignores external links", () => {
		render(
			<MemoryRouter initialEntries={["/"]}>
				<TestRoutes />
			</MemoryRouter>,
		);

		fireEvent.click(screen.getByRole("link", { name: "External docs" }));

		expect(
			screen.queryByRole("progressbar", { name: "Loading page" }),
		).not.toBeInTheDocument();
	});
});
