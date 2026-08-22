import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { RouteLoadError } from "./RouteLoadError";

describe("RouteLoadError", () => {
	it("offers recovery when an agent loader fails", async () => {
		window.history.replaceState({}, "", "/agents/broken");
		const router = createMemoryRouter(
			[
				{
					path: "/agents/broken",
					loader: () => {
						throw new Error("Agent request failed");
					},
					element: <div>Agent</div>,
					errorElement: <RouteLoadError />,
				},
			],
			{ initialEntries: ["/agents/broken"] },
		);

		render(<RouterProvider router={router} />);

		expect(
			await screen.findByRole("heading", {
				name: "Couldn't open this agent",
			}),
		).toBeInTheDocument();
		expect(screen.getByText("Agent request failed")).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /back/i })).toHaveAttribute(
			"href",
			"/agents",
		);
	});
});
