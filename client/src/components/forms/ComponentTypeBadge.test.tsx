import { it, expect } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ComponentTypeBadge } from "./ComponentTypeBadge";
it("identifies structural and field types with readable names and icons", () => {
	const { container } = renderWithProviders(
		<>
			<ComponentTypeBadge type="section" />
			<ComponentTypeBadge type="row" />
			<ComponentTypeBadge type="text" />
			<ComponentTypeBadge type="date" />
		</>,
	);
	for (const label of ["Section", "Row", "Text Input", "Date"])
		expect(screen.getByText(label)).toBeVisible();
	expect(container.querySelectorAll('svg[aria-hidden="true"]')).toHaveLength(
		4,
	);
});
