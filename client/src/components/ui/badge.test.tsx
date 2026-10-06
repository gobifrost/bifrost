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

	it("draws the power variant, for permissions, from the power tokens", () => {
		render(<Badge variant="power">Read Users</Badge>);

		const badge = screen.getByText("Read Users");
		expect(badge).toHaveClass(
			"bg-[var(--bf-power-soft)]",
			"text-[var(--bf-power)]",
		);
		expect(badge).not.toHaveClass("bg-secondary");
	});
});
