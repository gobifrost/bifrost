import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { PlaceLabel } from "./PlaceLabel";

describe("PlaceLabel", () => {
	it("shows the place's label with a decorative icon", () => {
		const { container } = render(
			<PlaceLabel
				place={{
					kind: "platform",
					organization_id: null,
					organization_name: null,
					label: "Global",
				}}
			/>,
		);

		expect(screen.getByText("Global")).toBeInTheDocument();
		expect(container.querySelector("svg")).toHaveAttribute(
			"aria-hidden",
			"true",
		);
	});
});
