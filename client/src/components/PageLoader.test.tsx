import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageLoader } from "./PageLoader";

describe("PageLoader", () => {
	it("announces progress and renders a reduced-motion-safe spinner", () => {
		const { container } = render(<PageLoader message="Loading agents…" />);

		expect(
			screen.getByRole("status", { name: "Loading agents…" }),
		).toHaveAttribute("aria-live", "polite");
		expect(container.querySelector("svg")).toHaveClass(
			"animate-spin",
			"motion-reduce:animate-none",
		);
	});
});
