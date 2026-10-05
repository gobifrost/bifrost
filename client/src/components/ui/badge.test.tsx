import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Badge } from "./badge";

describe("Badge", () => {
	it("draws the warning variant from the warning tokens", () => {
		render(<Badge variant="warning">Protected</Badge>);

		const badge = screen.getByText("Protected");
		expect(badge).toHaveClass(
			"bg-[var(--bf-warning-soft)]",
			"text-[var(--bf-warning)]",
		);
		expect(badge.className).not.toContain("amber");
	});
});
