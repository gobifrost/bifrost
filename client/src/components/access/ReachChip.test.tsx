import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { Place } from "@/services/access";

import { ReachChip } from "./ReachChip";

const places: Place[] = [
	{
		kind: "home",
		organization_id: "org-1",
		organization_name: "Contoso",
		label: "Contoso (Home)",
	},
	{
		kind: "organization",
		organization_id: "org-2",
		organization_name: "Fabrikam",
		label: "Fabrikam",
	},
	{
		kind: "managed_organizations",
		organization_id: null,
		organization_name: null,
		label: "All Customer Organizations",
	},
	{
		kind: "platform",
		organization_id: null,
		organization_name: null,
		label: "Global",
	},
];

describe("ReachChip", () => {
	it.each(places)("labels a $kind place", (place) => {
		render(<ReachChip place={place} />);

		const chip = screen.getByText(place.label).closest("[data-slot=badge]");
		expect(chip).toHaveAttribute("data-place", place.kind);
		expect(chip).toHaveClass("text-[var(--bf-reach)]");
	});

	it("keeps a long place on one line", () => {
		render(<ReachChip place={places[2]} />);

		const chip = screen
			.getByText("All Customer Organizations")
			.closest("[data-slot=badge]");
		expect(chip).toHaveClass("whitespace-nowrap", "max-w-full");
		expect(chip).not.toHaveClass("whitespace-normal");
		expect(screen.getByText("All Customer Organizations")).toHaveClass(
			"truncate",
		);
	});
});
