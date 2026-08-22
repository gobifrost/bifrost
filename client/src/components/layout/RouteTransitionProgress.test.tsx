import { act, fireEvent, render, screen } from "@testing-library/react";
import {
	Link,
	Outlet,
	RouterProvider,
	createMemoryRouter,
} from "react-router-dom";
import { describe, expect, it } from "vitest";

import { RouteTransitionProgress } from "./RouteTransitionProgress";

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((next) => {
		resolve = next;
	});
	return { promise, resolve };
}

function Shell() {
	return (
		<>
			<RouteTransitionProgress />
			<Link to="/agent">Agent</Link>
			<Outlet />
		</>
	);
}

describe("RouteTransitionProgress", () => {
	it("stays visible until the destination loader settles", async () => {
		const loader = deferred<null>();
		const router = createMemoryRouter(
			[
				{
					element: <Shell />,
					children: [
						{ index: true, element: <div>Fleet</div> },
						{
							path: "agent",
							loader: () => loader.promise,
							element: <div>Agent detail</div>,
						},
					],
				},
			],
			{ initialEntries: ["/"] },
		);

		render(<RouterProvider router={router} />);
		fireEvent.click(screen.getByRole("link", { name: "Agent" }));

		expect(screen.getByText("Fleet")).toBeInTheDocument();
		expect(
			screen.getByRole("progressbar", { name: "Loading page" }),
		).toBeInTheDocument();

		await act(async () => loader.resolve(null));
		expect(screen.getByText("Agent detail")).toBeInTheDocument();
		expect(
			screen.queryByRole("progressbar", { name: "Loading page" }),
		).not.toBeInTheDocument();
	});
});
