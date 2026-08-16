import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RouteOpeningState, openingKind } from "./RouteOpeningState";

describe("RouteOpeningState", () => {
	afterEach(() => window.history.replaceState({}, "", "/"));

	it("identifies detail routes for specific loading copy", () => {
		expect(openingKind("/agents/agent-1")).toBe("agent");
		expect(openingKind("/apps/my-app/preview")).toBe("application");
		expect(openingKind("/forms")).toBe("page");
	});

	it("shows immediate feedback for a direct agent load", () => {
		window.history.replaceState({}, "", "/agents/agent-1");
		render(<RouteOpeningState />);

		expect(
			screen.getByRole("status", { name: "Opening agent…" }),
		).toBeInTheDocument();
	});
});
